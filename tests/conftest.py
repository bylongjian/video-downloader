import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def public_dns(monkeypatch, tmp_path_factory):
    # 测试不能加载开发者 .env 中的真实密钥，也不能消耗模型额度。
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_PROXY_FAKE_IP", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path_factory.mktemp("accounts") / "accounts.db"))
    monkeypatch.setenv("JWT_SECRET", "test-only-secret-with-at-least-32-bytes")


@pytest.fixture
def ydl(monkeypatch):
    import yt_dlp

    options = []
    extract = Mock(return_value={})
    download_subtitle = Mock()

    class FakeYDL:
        def __init__(self, opts):
            options.append(opts)
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return extract(url, download=download, options=self.opts)

        def dl(self, path, info, subtitle=False):
            return download_subtitle(path, info, subtitle=subtitle)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return SimpleNamespace(extract=extract, options=options, download_subtitle=download_subtitle)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as value:
        yield value


@pytest.fixture
def auth_headers(client):
    data = client.post('/api/auth/register', json={'email': 'ai-reader@example.com', 'password': 'password123'}).json()['data']
    return {'Authorization': 'Bearer ' + data['token']}


@pytest.fixture
def model(monkeypatch):
    from backend import summarizer

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    create = Mock()
    stream = Mock()
    stream.__iter__ = Mock(return_value=iter([
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="第一段\n"), finish_reason=None)]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="第二段"), finish_reason="stop")]),
    ]))
    create.side_effect = lambda **kwargs: stream if kwargs["stream"] else SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="# 主题\n## 要点"), finish_reason="stop")]
    )
    constructor = Mock(return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=Mock()))
    monkeypatch.setattr(summarizer, "OpenAI", constructor)
    return SimpleNamespace(create=create, constructor=constructor, stream=stream)
