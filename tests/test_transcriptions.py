import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import database, tasks
from backend.auth import get_current_user
from backend.summarizer import subtitle_result


@pytest.fixture
def transcription_setup(monkeypatch, tmp_path):
    from backend import transcriber
    from backend.api_transcriptions import router
    database.init_db()
    user = database.create_user('transcription@example.com', 'unused')['id']
    monkeypatch.setattr(tasks, 'QUEUE_ROOT', tmp_path / 'queue')
    monkeypatch.setattr(tasks, '_stopping', __import__('threading').Event())
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    for name in ('model.bin', 'config.json', 'tokenizer.json'):
        (model_dir / name).write_text('{}')
    monkeypatch.setenv('ASR_ENABLED', 'true')
    monkeypatch.setenv('ASR_MODEL_DIR', str(model_dir))
    transcriber._load_local_model.cache_clear()
    asr = Mock()
    asr.transcribe.side_effect = lambda *a, **kw: (iter([
        SimpleNamespace(start=0, end=1.5, text=' Hello'),
        SimpleNamespace(start=1.5, end=3, text='world '),
    ]), SimpleNamespace(language='en'))
    constructor = Mock(return_value=asr)
    download_model = Mock(side_effect=AssertionError('ordinary requests must never download models'))
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=constructor, download_model=download_model))
    platform = Mock(return_value=subtitle_result([]))
    monkeypatch.setattr(transcriber.SubtitleExtractor, 'extract', platform)
    monkeypatch.setattr(transcriber.VideoDownloader, '_extract', lambda *a, **kw: {'duration': None})
    download = Mock()
    def audio_download(self, url, format_id, **kwargs):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        path = self.download_dir / 'audio.webm'
        path.write_bytes(b'audio')
        download(format_id, **kwargs)
        return {'filepath': str(path)}
    monkeypatch.setattr(transcriber.VideoDownloader, 'download', audio_download)
    probe = SimpleNamespace(duration=60.1)
    def command(args, **kwargs):
        if '-show_entries' in args:
            return SimpleNamespace(stdout=json.dumps({'format': {'duration': str(probe.duration)}, 'streams': [{'codec_type': 'audio'}]}))
        from pathlib import Path
        Path(args[-1]).write_bytes(b'wav')
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(transcriber.subprocess, 'run', command)
    monkeypatch.setattr(transcriber.shutil, 'which', lambda name: name)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as api:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user)
        yield SimpleNamespace(user=user, api=api, asr=asr, constructor=constructor, platform=platform,
                              download=download, probe=probe, transcriber=transcriber)
    transcriber._load_local_model.cache_clear()


def submit_and_run(setup, key, *, force=False, url='https://example.com/video'):
    response = setup.api.post('/api/transcriptions', json={'url': url, 'request_key': key, 'language': 'auto', 'force': force})
    assert response.status_code == 202
    task_id = response.json()['data']['task']['id']
    with database.get_db() as db:
        db.execute("UPDATE tasks SET state='running',started_at=? WHERE id=?", (int(time()), task_id))
    setup.transcriber.run_transcription_task(task_id)
    return tasks.get_task(setup.user, task_id)


def test_transcription_http_concurrent_submit_success_cache_owner_delete(transcription_setup):
    s = transcription_setup
    barrier = Barrier(2)
    def submit(index):
        barrier.wait()
        return s.transcriber.submit_transcription(s.user, 'https://example.com/video', 'auto', f'parallel-{index}', bool(index))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(submit, (0, 1)))
    assert first['task']['id'] == second['task']['id']
    task = submit_and_run(s, 'parallel-0')
    assert task['state'] == 'succeeded'
    result = s.api.get('/api/transcriptions/result', params={'url': task['url']}).json()['data']
    assert result['audio_seconds'] == 61
    assert result['subtitle']['full_text'] == 'Hello world'
    assert result['subtitle']['source'] == 'transcription'
    assert result['subtitle']['segments'][0] == {'start': 0, 'end': 1.5, 'text': 'Hello'}
    cached = s.api.post('/api/transcriptions', json={'url': task['url'], 'request_key': 'cache'})
    assert cached.status_code == 200 and cached.json()['data']['result'] == result
    options = s.constructor.call_args.kwargs
    assert options['device'] == 'cpu' and options['compute_type'] == 'int8' and options['local_files_only'] is True
    assert s.asr.transcribe.call_count == 1
    assert not (tasks.QUEUE_ROOT / task['id']).exists()
    from backend.extension_quota import get_extension_quota
    assert get_extension_quota(s.user)['transcription']['used'] == 61
    other = database.create_user('other-transcription@example.com', 'unused')['id']
    s.api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=other)
    assert s.api.get('/api/transcriptions/result', params={'url': task['url']}).json()['data'] is None
    assert s.api.delete('/api/transcriptions/' + result['id']).status_code == 404
    s.api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=s.user)
    from backend.subtitles import get_subtitle_source
    assert get_subtitle_source(s.user, 'transcription', result['id'])['subtitle'] == result['subtitle']
    assert s.transcriber.get_transcription_by_id(s.user, result['id']) == result
    assert s.api.delete('/api/transcriptions/' + result['id']).json()['data'] == {'deleted': True}
    assert get_extension_quota(s.user)['transcription']['used'] == 61


def test_platform_subtitles_and_missing_model_never_download_audio_or_charge(transcription_setup, monkeypatch):
    s = transcription_setup
    s.platform.return_value = subtitle_result([{'start': 0, 'end': 1, 'text': 'platform'}])
    task = submit_and_run(s, 'has-subtitle')
    assert task['error']['code'] == 'SUBTITLE_AVAILABLE'
    s.platform.return_value = subtitle_result([])
    monkeypatch.delenv('ASR_MODEL_DIR')
    task = submit_and_run(s, 'missing-model')
    assert task['error']['code'] == 'ASR_NOT_READY'
    s.download.assert_not_called()
    s.asr.transcribe.assert_not_called()
    from backend.extension_quota import get_extension_quota
    assert get_extension_quota(s.user)['transcription']['used'] == 0


def test_transcription_overlong_silent_cancel_timeout_keep_previous_and_release(transcription_setup):
    s = transcription_setup
    original = submit_and_run(s, 'original')
    saved = s.transcriber.get_transcription(s.user, original['url'])
    from backend.extension_quota import get_extension_quota
    for scenario in ('overlong', 'silent', 'cancel', 'timeout', 'expiry','bad_time'):
        s.probe.duration = 1800.01 if scenario == 'overlong' else 60.1
        def recognize(*args, **kwargs):
            def segments():
                if scenario == 'cancel':
                    with database.get_db() as db:
                        task_id = db.execute("SELECT id FROM tasks WHERE state='running'").fetchone()[0]
                    tasks.cancel_task(s.user, task_id)
                elif scenario == 'timeout':
                    with database.get_db() as db:
                        db.execute("UPDATE tasks SET started_at=0 WHERE state='running'")
                elif scenario == 'expiry':
                    with database.get_db() as db:
                        db.execute("UPDATE extension_usage_requests SET expires_at=0 WHERE status='reserved'")
                if scenario != 'silent':
                    yield SimpleNamespace(start=1700 if scenario=='bad_time' else 0, end=1750 if scenario=='bad_time' else 1, text='new speech')
            return segments(), SimpleNamespace(language='en')
        s.asr.transcribe.side_effect = recognize
        task = submit_and_run(s, scenario, force=True)
        assert task['state'] == ('cancelled' if scenario == 'cancel' else 'failed')
        if scenario != 'cancel':
            assert task['error']['code'] == {'overlong': 'AUDIO_TOO_LONG', 'silent': 'NO_SPEECH', 'timeout': 'TASK_TIMEOUT', 'expiry': 'TASK_TIMEOUT','bad_time':'ASR_FAILED'}[scenario]
        assert s.transcriber.get_transcription(s.user, original['url']) == saved
        assert not (tasks.QUEUE_ROOT / task['id']).exists()
        usage = get_extension_quota(s.user)['transcription']
        assert usage['used'] == 61 and usage['reserved'] == 0
    # 成功重写沿用来源 ID，旧版本译文清理；失败分支已证明旧版本保留。
    from backend.subtitles import get_subtitle_source, save_translation
    source = get_subtitle_source(s.user, 'transcription', saved['id'])
    with database.get_db() as db:
        save_translation(db, s.user, source, 'zh', saved['subtitle'])
    s.asr.transcribe.side_effect = lambda *a, **kw: (iter([SimpleNamespace(start=0, end=2, text='new words')]), SimpleNamespace(language='en'))
    assert submit_and_run(s, 'new-success', force=True)['state'] == 'succeeded'
    updated = s.transcriber.get_transcription(s.user, original['url'])
    assert updated['id'] == saved['id'] and updated['subtitle']['full_text'] == 'new words'
    with database.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM subtitle_translations').fetchone()[0] == 0
    assert get_extension_quota(s.user)['transcription']['used'] == 122
