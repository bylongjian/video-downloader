import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError


URL = "https://www.youtube.com/watch?v=sample"
VTT = "WEBVTT\n\n00:01.200 --> 00:02.340\nHello &amp; <b>world</b>\n\n01:00:01.000 --> 01:00:02.000\nHello again\n"


def events(response):
    parsed = []
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        name = None
        value = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            if line.startswith("data:"):
                value.append(line[5:].lstrip())
        if name:
            raw = "\n".join(value)
            parsed.append((name, raw if raw == "[DONE]" else json.loads(raw)))
    return parsed


def subtitles(ydl, monkeypatch, content=VTT):
    ydl.extract.return_value = {"subtitles": {"en": [{"ext": "vtt", "url": "https://cdn.example.com/sub.vtt"}]}}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, text=content, request=httpx.Request("GET", url)))


def test_manual_other_language_beats_auto_chinese_and_vtt_times(ydl, monkeypatch):
    from backend.summarizer import SubtitleExtractor

    subtitles(ydl, monkeypatch)
    ydl.extract.return_value = {
        "subtitles": {"fr": [{"ext": "vtt", "url": "https://cdn.example.com/fr.vtt"}]},
        "automatic_captions": {"zh": [{"ext": "vtt", "url": "https://cdn.example.com/zh.vtt"}]},
    }
    data = SubtitleExtractor().extract(URL)
    assert data["language"] == "fr" and data["subtitle_type"] == "manual"
    assert data["segments"] == [{"start": 1.2, "end": 2.34, "text": "Hello & world"}, {"start": 3601.0, "end": 3602.0, "text": "Hello again"}]


@pytest.mark.parametrize("source", ["inline", "segmented"])
def test_bilibili_api_failure_falls_back_to_ytdlp(ydl, monkeypatch, source):
    from backend.summarizer import SubtitleExtractor

    subtitles(ydl, monkeypatch)
    ydl.extract.return_value = {"subtitles": {"en": [{"ext": "srt", "data": "1\n00:00:01,200 --> 00:00:02,340\nHello world\n\n"}]}}
    if source == "segmented":
        ydl.extract.return_value = {"subtitles": {"en": [{"ext": "vtt", "protocol": "m3u8_native", "url": "https://cdn.example.com/subtitles.m3u8"}]}}
        def download(path, info, subtitle):
            assert subtitle is True
            Path(path).write_text("WEBVTT\n\n00:00:01.200 --> 00:00:02.340\nHello world\n\n", encoding="utf-8")
            return None, True
        ydl.download_subtitle.side_effect = download
    def get(url, **kwargs):
        if "api.bilibili.com" in url:
            raise httpx.ConnectTimeout("timeout")
        if url.endswith(".m3u8"):
            return httpx.Response(200, text="#EXTM3U\n#EXTINF:10,\nsegment.vtt\n", request=httpx.Request("GET", url))
        return httpx.Response(200, text=VTT, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    data = SubtitleExtractor().extract("https://www.bilibili.com/video/BV1sample")
    assert data["has_subtitle"] is True
    assert data["source"] == "yt-dlp"
    assert data["segments"] == [{"start": 1.2, "end": 2.34, "text": "Hello world"}]
    assert ydl.options[0]["writesubtitles"] is True
    assert ydl.options[0]["writeautomaticsub"] is True
    assert ydl.extract.call_args.kwargs["download"] is False


def test_empty_captions_are_not_reported_as_available(ydl, monkeypatch):
    from backend.summarizer import SubtitleExtractor

    subtitles(ydl, monkeypatch, "WEBVTT\n\n")
    data = SubtitleExtractor().extract(URL)
    assert data["has_subtitle"] is False
    assert data["subtitle_type"] == "none"


def test_summary_stream_has_subtitles_tokens_mindmap_done(client, ydl, monkeypatch, model):
    subtitles(ydl, monkeypatch)
    response = client.post("/api/summarize", json={"url": URL})
    stream = events(response)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [name for name, _ in stream] == ["subtitle", "summary", "summary", "mindmap", "done"]
    assert stream[1][1] == "第一段\n"
    assert stream[3][1] == {"markdown": "# 主题\n## 要点"}
    assert model.create.call_args_list[0].kwargs["model"] == "deepseek-flash"
    assert all(call.kwargs["extra_body"] == {"thinking": {"type": "disabled"}} for call in model.create.call_args_list)


def test_missing_ai_key_still_delivers_subtitles(client, ydl, monkeypatch):
    subtitles(ydl, monkeypatch)
    stream = events(client.post("/api/summarize", json={"url": URL}))
    assert [name for name, _ in stream] == ["subtitle", "error"]
    assert "DEEPSEEK_API_KEY" in stream[-1][1]["message"]


def test_summary_no_subtitle_explains_limitation(client, ydl):
    ydl.extract.return_value = {"subtitles": {}}
    stream = events(client.post("/api/summarize", json={"url": URL}))
    assert [name for name, _ in stream] == ["subtitle", "error"]
    assert stream[0][1]["has_subtitle"] is False
    assert "字幕" in stream[-1][1]["message"]


def test_chat_uses_supplied_context_and_multiround_history(client, ydl, model):
    history = [{"role": "user", "content": "讲什么？"}, {"role": "assistant", "content": "讲 Python"}]
    stream = events(client.post("/api/chat", json={"url": URL, "question": "举个例子", "subtitle_text": "字幕正文", "history": history}))
    assert [name for name, _ in stream] == ["answer", "answer", "done"]
    messages = model.create.call_args.kwargs["messages"]
    assert messages[1:3] == history
    assert "字幕正文" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "举个例子"}
    ydl.extract.assert_not_called()


def test_chat_without_available_context_returns_error(client, ydl):
    ydl.extract.return_value = {}
    stream = events(client.post("/api/chat", json={"url": URL, "question": "内容是什么？"}))
    assert [name for name, _ in stream] == ["error"]
    assert "字幕" in stream[0][1]["message"]


@pytest.mark.parametrize("failure", ["network", "empty", "length"])
def test_chat_model_failure_never_reports_success(client, model, failure):
    if failure == "network":
        model.create.side_effect = APIConnectionError(request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"))
    else:
        model.stream.__iter__.return_value = iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="" if failure == "empty" else "部分回答"), finish_reason="stop" if failure == "empty" else "length")])])
    stream = events(client.post("/api/chat", json={"url": URL, "question": "内容？", "subtitle_text": "字幕"}))
    assert stream[-1][0] == "error"
    assert "done" not in [name for name, _ in stream]
    assert "AI" in stream[-1][1]["message"]
