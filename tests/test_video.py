from contextlib import nullcontext
from pathlib import Path
import socket

import httpx
from yt_dlp.utils import DownloadError


URL = "https://www.youtube.com/watch?v=sample"


def test_thumbnail_returns_only_image_bytes(client, monkeypatch):
    monkeypatch.setattr(httpx, "stream", lambda method, url, **kwargs: nullcontext(httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/jpeg"}, request=httpx.Request("GET", url))))
    response = client.get("/api/proxy/thumbnail", params={"url": "https://cdn.example.com/image.jpg"})
    assert response.status_code == 200
    assert response.content == b"image-bytes"
    assert response.headers["content-type"] == "image/jpeg"


def test_thumbnail_rejects_private_redirect_and_non_image(client, monkeypatch):
    calls = []
    def redirect(method, url, **kwargs):
        calls.append(url)
        return nullcontext(httpx.Response(302, headers={"location": "http://127.0.0.1/private"}, request=httpx.Request("GET", url)))
    monkeypatch.setattr(httpx, "stream", redirect)
    response = client.get("/api/proxy/thumbnail", params={"url": "https://cdn.example.com/image.jpg"})
    assert response.status_code == 400
    assert len(calls) == 1
    monkeypatch.setattr(httpx, "stream", lambda method, url, **kwargs: nullcontext(httpx.Response(200, content=b"<html>not image</html>", headers={"content-type": "text/html"}, request=httpx.Request("GET", url))))
    response = client.get("/api/proxy/thumbnail", params={"url": "https://cdn.example.com/image.jpg"})
    assert response.status_code == 400


def test_parse_metadata_and_mux_formats(client, ydl):
    ydl.extract.return_value = {
        "id": "sample", "title": "示例", "duration": 212, "extractor_key": "Youtube",
        "subtitles": {"en": []}, "formats": [
            {"format_id": "137", "ext": "mp4", "height": 1080, "width": 1920, "vcodec": "avc1", "acodec": "none"},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a"},
            {"format_id": "18", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a"},
        ],
    }
    response = client.post("/api/parse", json={"url": URL})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["duration_string"] == "3:32"
    assert data["formats"][0]["format_id"] == "137+140"
    assert data["formats"][0]["has_audio"] is True
    assert data["subtitles"] == ["en"]
    ydl.extract.return_value = {"id": "direct", "title": "direct", "url": "https://cdn.example.com/direct.mp4", "ext": "mp4", "format_id": "mp4"}
    direct = client.post("/api/parse", json={"url": URL}).json()["data"]["formats"][0]
    assert direct["has_audio"] is None
    assert "无音频" not in direct["label"]
    assert direct["resolution"] == "未知"


def test_parse_fake_dns_requires_opt_in_and_still_rejects_private_targets(client, ydl, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("198.18.0.102", 443))])
    assert client.post("/api/parse", json={"url": URL}).status_code == 400
    ydl.extract.assert_not_called()
    monkeypatch.setenv("ALLOW_PROXY_FAKE_IP", "true")
    ydl.extract.return_value = {"id": "sample", "title": "公开视频", "formats": []}
    assert client.post("/api/parse", json={"url": URL}).status_code == 200
    ydl.extract.reset_mock()
    for url in ["http://127.0.0.1:9000/private", "http://192.168.1.1/", "http://198.18.0.102/video", "http://localhost/video"]:
        response = client.post("/api/parse", json={"url": url})
        assert response.status_code == 400
        assert response.json()["success"] is False
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("192.168.1.1", 443))])
    assert client.post("/api/parse", json={"url": URL}).status_code == 400
    ydl.extract.assert_not_called()


def test_parse_upstream_failure_is_readable(client, ydl):
    ydl.extract.side_effect = DownloadError("not available")
    response = client.post("/api/parse", json={"url": URL})
    assert response.status_code == 400
    assert response.json() == {"success": False, "error": "视频解析失败，请检查链接或稍后重试"}


def test_direct_url_combined_and_header_requirements(client, ydl):
    ydl.extract.return_value = {"url": "https://cdn.example.com/a.mp4", "ext": "mp4", "http_headers": {"Referer": URL}}
    response = client.post("/api/direct-url", json={"url": URL, "format_id": "18"})
    assert response.json()["data"]["requires_proxy"] is True
    ydl.extract.return_value["http_headers"] = {}
    response = client.post("/api/direct-url", json={"url": URL, "format_id": "18"})
    assert response.json()["data"]["requires_proxy"] is False
    assert response.json()["data"]["direct_url"].endswith("a.mp4")


def test_direct_url_never_returns_video_only_for_merge(client, ydl):
    ydl.extract.return_value = {"ext": "mp4", "requested_formats": [{"url": "https://cdn.example.com/video"}, {"url": "https://cdn.example.com/audio"}]}
    response = client.post("/api/direct-url", json={"url": URL, "format_id": "137+140"})
    data = response.json()["data"]
    assert data["requires_proxy"] is True
    assert data["direct_url"] is None


def test_download_returns_actual_mux_file_and_removes_task_dir(client, ydl, monkeypatch, tmp_path):
    from backend.main import downloader

    monkeypatch.setattr(downloader, "download_dir", tmp_path)
    monkeypatch.setattr(downloader, "ffmpeg_path", "ffmpeg")
    paths = []

    def extract(url, download, options):
        path = Path(options["outtmpl"]).parent / "media.mkv"
        path.write_bytes(b"muxed video and audio")
        paths.append(path)
        return {"title": "合并视频", "ext": "webm", "filepath": str(path)}

    ydl.extract.side_effect = extract
    response = client.post("/api/download", json={"url": URL, "format_id": "137+140"})
    assert response.content == b"muxed video and audio"
    assert ".mkv" in response.headers["content-disposition"]
    assert not paths[0].parent.exists()


def test_download_without_ffmpeg_uses_combined_and_warns(client, ydl, monkeypatch, tmp_path):
    from backend.main import downloader

    monkeypatch.setattr(downloader, "download_dir", tmp_path)
    monkeypatch.setattr(downloader, "ffmpeg_path", None)

    def extract(url, download, options):
        assert options["format"] == "best[vcodec!=none][acodec!=none]"
        path = Path(options["outtmpl"]).parent / "media.mp4"
        path.write_bytes(b"combined")
        return {"title": "lower resolution", "ext": "mp4", "filepath": str(path)}

    ydl.extract.side_effect = extract
    response = client.post("/api/download", json={"url": URL, "format_id": "137+140"})
    assert response.status_code == 200
    assert "ffmpeg" in response.headers["x-download-warning"].lower()


def test_failed_download_removes_partial_files(client, ydl, monkeypatch, tmp_path):
    from backend.main import downloader

    monkeypatch.setattr(downloader, "download_dir", tmp_path)

    def extract(url, download, options):
        assert options["skip_unavailable_fragments"] is False
        (Path(options["outtmpl"]).parent / "media.part").write_bytes(b"partial")
        raise DownloadError("interrupted")

    ydl.extract.side_effect = extract
    response = client.post("/api/download", json={"url": URL, "format_id": "18"})
    assert response.status_code == 400
    assert list(tmp_path.iterdir()) == []
