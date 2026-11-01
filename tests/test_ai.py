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


def test_summary_stream_has_subtitles_tokens_mindmap_done(client, auth_headers, ydl, monkeypatch, model):
    subtitles(ydl, monkeypatch)
    response = client.post("/api/summarize", headers=auth_headers, json={"url": URL})
    stream = events(response)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [name for name, _ in stream] == ["subtitle", "summary", "summary", "mindmap", "quota", "done"]
    assert stream[1][1] == "第一段\n"
    assert stream[3][1] == {"markdown": "# 主题\n## 要点"}
    assert stream[-2][1]['summary']=={'limit':3,'used':1,'reserved':0,'remaining':2}
    assert stream[-2][1]['chat']['remaining']==10
    assert model.create.call_args_list[0].kwargs["model"] == "deepseek-flash"
    assert all(call.kwargs["extra_body"] == {"thinking": {"type": "disabled"}} for call in model.create.call_args_list)


def test_missing_ai_key_still_delivers_subtitles(client, auth_headers, ydl, monkeypatch):
    subtitles(ydl, monkeypatch)
    stream = events(client.post("/api/summarize", headers=auth_headers, json={"url": URL}))
    assert [name for name, _ in stream] == ["subtitle", "error"]
    assert "DEEPSEEK_API_KEY" in stream[-1][1]["message"]
    assert client.get('/api/auth/quota',headers=auth_headers).json()['data']['summary']['remaining']==3


def test_summary_no_subtitle_explains_limitation(client, auth_headers, ydl):
    ydl.extract.return_value = {"subtitles": {}}
    stream = events(client.post("/api/summarize", headers=auth_headers, json={"url": URL}))
    assert [name for name, _ in stream] == ["subtitle", "error"]
    assert stream[0][1]["has_subtitle"] is False
    assert "字幕" in stream[-1][1]["message"]
    assert client.get('/api/auth/quota',headers=auth_headers).json()['data']['summary']['reserved']==0


def test_chat_uses_supplied_context_and_multiround_history(client, auth_headers, ydl, model):
    history = [{"role": "user", "content": "讲什么？"}, {"role": "assistant", "content": "讲 Python"}]
    stream = events(client.post("/api/chat", headers=auth_headers, json={"url": URL, "question": "举个例子", "subtitle_text": "字幕正文", "history": history}))
    assert [name for name, _ in stream] == ["answer", "answer", "quota", "done"]
    messages = model.create.call_args.kwargs["messages"]
    assert messages[1:3] == history
    assert "字幕正文" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "举个例子"}
    assert stream[-2][1]['chat']['remaining']==9
    assert stream[-2][1]['summary']['remaining']==3
    ydl.extract.assert_not_called()


def test_chat_without_available_context_returns_error(client, auth_headers, ydl):
    ydl.extract.return_value = {}
    stream = events(client.post("/api/chat", headers=auth_headers, json={"url": URL, "question": "内容是什么？"}))
    assert [name for name, _ in stream] == ["error"]
    assert "字幕" in stream[0][1]["message"]


@pytest.mark.parametrize("failure", ["network", "empty", "length"])
def test_chat_model_failure_never_reports_success(client, auth_headers, model, failure):
    if failure == "network":
        model.create.side_effect = APIConnectionError(request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"))
    else:
        model.stream.__iter__.return_value = iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="" if failure == "empty" else "部分回答"), finish_reason="stop" if failure == "empty" else "length")])])
    stream = events(client.post("/api/chat", headers=auth_headers, json={"url": URL, "question": "内容？", "subtitle_text": "字幕"}))
    assert stream[-1][0] == "error"
    assert "done" not in [name for name, _ in stream]
    assert "AI" in stream[-1][1]["message"]
    assert client.get('/api/auth/quota',headers=auth_headers).json()['data']['chat']['remaining']==10


def test_anonymous_ai_requests_are_rejected_before_work(client, ydl, model):
    for path,body in [('summarize',{'url':URL}),('chat',{'url':URL,'question':'内容？'})]:
        response=client.post('/api/'+path,json=body)
        assert response.status_code==401
        assert response.json()['code']=='AUTH_REQUIRED'
        assert response.headers['content-type'].startswith('application/json')
    ydl.extract.assert_not_called()
    model.create.assert_not_called()


@pytest.mark.parametrize('stage',['summary','mindmap'])
def test_summary_model_failure_releases_reservation(client, auth_headers, ydl, monkeypatch, model, stage):
    subtitles(ydl,monkeypatch)
    from backend.downloader import VideoError
    from backend.summarizer import VideoSummarizer
    if stage=='mindmap':
        monkeypatch.setattr(VideoSummarizer,'generate_mindmap',lambda *args: (_ for _ in ()).throw(VideoError('AI 导图失败')))
    else:
        model.create.side_effect=APIConnectionError(request=httpx.Request('POST','https://api.deepseek.com'))
    stream=events(client.post('/api/summarize',headers=auth_headers,json={'url':URL}))
    assert stream[-1][0]=='error'
    assert 'done' not in [name for name,_ in stream]
    assert client.get('/api/auth/quota',headers=auth_headers).json()['data']['summary']=={'limit':3,'used':0,'reserved':0,'remaining':3}


def test_chat_exhausted_quota_rejected_before_model(client,auth_headers,ydl,model):
    from backend import quota
    user=client.get('/api/auth/me',headers=auth_headers).json()['data']['user']['id']
    for _ in range(10):
        quota.commit_usage(quota.reserve_usage(user,'chat').request_id)
    response=client.post('/api/chat',headers=auth_headers,json={'url':URL,'question':'内容？','subtitle_text':'字幕'})
    assert response.status_code==429
    assert response.json()['code']=='QUOTA_EXHAUSTED'
    ydl.extract.assert_not_called()
    model.create.assert_not_called()


def test_saved_summary_survives_relogin_and_restart_without_usage(client,auth_headers,ydl,monkeypatch,model):
    from fastapi.testclient import TestClient
    from backend.main import app

    subtitles(ydl,monkeypatch)
    assert events(client.post('/api/summarize',headers=auth_headers,json={'url':URL}))[-1][0]=='done'
    expected={'summary':'第一段\n第二段','mindmap':'# 主题\n## 要点'}
    original=client.get('/api/summarize/result',headers=auth_headers,params={'url':URL}).json()['data']
    assert original['summary']==expected['summary'] and original['mindmap']==expected['mindmap']
    assert original['subtitle']['full_text']=='Hello & world Hello again'
    assert client.get('/api/summarize/result',params={'url':URL}).status_code==401
    other=client.post('/api/auth/register',json={'email':'other-reader@example.com','password':'password123'}).json()['data']
    assert client.get('/api/summarize/result',headers={'Authorization':'Bearer '+other['token']},params={'url':URL}).json()['data'] is None
    client.post('/api/auth/logout',headers=auth_headers)
    model.create.reset_mock();ydl.extract.reset_mock()
    with TestClient(app) as restarted:
        headers={'Authorization':'Bearer '+restarted.post('/api/auth/login',json={'email':'ai-reader@example.com','password':'password123'}).json()['data']['token']}
        for _ in range(3):
            assert restarted.get('/api/summarize/result',headers=headers,params={'url':URL}).json()['data']==original
        assert restarted.get('/api/auth/quota',headers=headers).json()['data']['summary']['used']==1
        assert restarted.get('/api/summarize/result',headers=headers,params={'url':URL,'language':'en'}).json()['data'] is None
    model.create.assert_not_called();ydl.extract.assert_not_called()


def test_failed_regeneration_preserves_previous_result(client,auth_headers,ydl,monkeypatch,model):
    subtitles(ydl,monkeypatch)
    client.post('/api/summarize',headers=auth_headers,json={'url':URL})
    original=client.get('/api/summarize/result',headers=auth_headers,params={'url':URL}).json()['data']
    model.create.side_effect=APIConnectionError(request=httpx.Request('POST','https://api.deepseek.com'))
    assert events(client.post('/api/summarize',headers=auth_headers,json={'url':URL}))[-1][0]=='error'
    assert client.get('/api/summarize/result',headers=auth_headers,params={'url':URL}).json()['data']==original
    assert client.get('/api/auth/quota',headers=auth_headers).json()['data']['summary']['used']==1
    model.create.side_effect=lambda **kwargs:model.stream if kwargs['stream'] else SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='# 新导图'),finish_reason='stop')])
    model.stream.__iter__.return_value=iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='更新摘要'),finish_reason='stop')])])
    assert events(client.post('/api/summarize',headers=auth_headers,json={'url':URL}))[-1][0]=='done'
    assert client.get('/api/summarize/result',headers=auth_headers,params={'url':URL}).json()['data']['mindmap']=='# 新导图'
    assert client.get('/api/auth/quota',headers=auth_headers).json()['data']['summary']['used']==2


def test_result_storage_failure_rolls_back_usage(client,auth_headers,ydl,monkeypatch,model):
    import sqlite3
    from backend import database
    subtitles(ydl,monkeypatch)
    def unavailable(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(database,'save_summary_result',unavailable)
    stream=events(client.post('/api/summarize',headers=auth_headers,json={'url':URL}))
    assert stream[-1][0]=='error' and 'done' not in [name for name,_ in stream]
    assert client.get('/api/auth/quota',headers=auth_headers).json()['data']['summary']=={'limit':3,'used':0,'reserved':0,'remaining':3}
    assert client.get('/api/summarize/result',headers=auth_headers,params={'url':URL}).json()['data'] is None


@pytest.mark.parametrize('phase',['between_tokens','during_next'])
def test_disconnect_releases_usage_and_closes_model(client,auth_headers,model,phase):
    import asyncio
    import threading
    from backend.main import app
    from backend import database

    blocked=threading.Event()
    finish=threading.Event()
    def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='first'),finish_reason=None)])
        if phase=='during_next':
            blocked.set()
            assert finish.wait(5)
        else:
            for _ in range(100):
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='next'),finish_reason=None)])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=''),finish_reason='stop')])
    model.stream.__iter__.side_effect=chunks
    async def disconnect_request():
        disconnected=asyncio.Event()
        body=json.dumps({'url':URL,'question':'内容？','subtitle_text':'字幕'}).encode()
        sent=False
        async def receive():
            nonlocal sent
            if not sent:
                sent=True
                return {'type':'http.request','body':body,'more_body':False}
            await disconnected.wait()
            asyncio.get_running_loop().call_later(.05,finish.set)
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type']=='http.response.body' and b'event: answer' in message.get('body',b''):
                if phase=='during_next':
                    assert await asyncio.to_thread(blocked.wait,3)
                disconnected.set()
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.0'},'http_version':'1.1','method':'POST','scheme':'http','path':'/api/chat','raw_path':b'/api/chat','root_path':'','query_string':b'','server':('testserver',80),'client':('127.0.0.1',1234),'headers':[(b'content-type',b'application/json'),(b'authorization',auth_headers['Authorization'].encode())]}
        await app(scope,receive,send)
    asyncio.run(disconnect_request())
    with database.get_db() as db:
        assert db.execute('SELECT status FROM ai_usage_requests').fetchone()['status']=='released'
    model.stream.close.assert_called()
    model.constructor.return_value.close.assert_called()
