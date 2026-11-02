"""用户明确提交后才下载音频；本地模型只读，成功字幕与秒数一起保存。"""

import argparse
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
from contextlib import nullcontext
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from time import time
from typing import Literal

from fastapi import HTTPException
from yt_dlp.utils import DownloadError

from . import extension_quota, tasks
from .database import get_db
from .downloader import DownloadCancelled, DownloadLimitExceeded, VideoDownloader, VideoError, validate_public_url
from .quota import QuotaError
from .summarizer import SubtitleExtractor, subtitle_result

Language = Literal['auto', 'zh', 'en']
MAX_AUDIO_SECONDS = 1800
MODEL_NAME = 'small'


class TranscriptionError(VideoError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _result(row) -> dict:
    return {'id': row['id'], 'url': row['url'], 'subtitle': json.loads(row['result_json']),
            'audio_seconds': row['audio_seconds'], 'model_name': row['model_name'],
            'saved_at': datetime.fromtimestamp(row['updated_at'], timezone.utc).isoformat()}


def get_transcription(user_id: int, url: str) -> dict | None:
    with get_db() as db:
        row = db.execute('SELECT * FROM transcription_results WHERE user_id=? AND url=?', (user_id, url)).fetchone()
        return _result(row) if row else None


def get_transcription_by_id(user_id: int, transcription_id: str, *, db=None) -> dict:
    with nullcontext(db) if db is not None else get_db() as connection:
        row = connection.execute('SELECT * FROM transcription_results WHERE user_id=? AND id=?', (user_id, transcription_id)).fetchone()
        if row is None:
            raise HTTPException(404, {'code': 'TRANSCRIPTION_NOT_FOUND', 'error': '转写结果不存在或无权访问'})
        return _result(row)


def submit_transcription(user_id: int, url: str, language: Language, request_key: str, force: bool = False) -> dict:
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT id,kind FROM tasks WHERE user_id=? AND request_key=?', (user_id, request_key)).fetchone()
        if existing:
            if existing['kind'] != 'transcription':
                raise HTTPException(409, {'code': 'REQUEST_KEY_CONFLICT', 'error': '该请求标识已用于其他任务，请重新提交'})
            return {'cached': False, 'task': tasks.get_task(user_id, existing['id'])}
        active = db.execute("SELECT id FROM tasks WHERE user_id=? AND kind='transcription' AND source_key=? AND state IN ('queued','running','cancelling')", (user_id, url)).fetchone()
        if active:
            return {'cached': False, 'task': tasks.get_task(user_id, active['id'])}
        cached = db.execute('SELECT * FROM transcription_results WHERE user_id=? AND url=?', (user_id, url)).fetchone()
        if cached and not force:
            return {'cached': True, 'result': _result(cached)}
        task = tasks.create_task(user_id, 'transcription', {'url': url, 'language': language, 'force': force}, request_key, source_key=url, db=db)
        return {'cached': False, 'task': task}


def delete_transcription(user_id: int, transcription_id: str) -> dict:
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT url FROM transcription_results WHERE user_id=? AND id=?', (user_id, transcription_id)).fetchone()
        if row is None:
            raise HTTPException(404, {'code': 'TRANSCRIPTION_NOT_FOUND', 'error': '转写结果不存在或无权访问'})
        busy = db.execute("""SELECT 1 FROM tasks WHERE user_id=? AND state IN ('queued','running','cancelling') AND
            ((kind='transcription' AND source_key=?) OR (kind='translation' AND json_extract(input_json,'$.source_kind')='transcription'
            AND json_extract(input_json,'$.source_id')=?))""", (user_id, row['url'], transcription_id)).fetchone()
        if busy:
            raise HTTPException(409, {'code': 'SOURCE_BUSY', 'error': '该字幕仍有处理任务，请先取消任务再删除'})
        db.execute("DELETE FROM subtitle_translations WHERE user_id=? AND source_kind='transcription' AND source_id=?", (user_id, transcription_id))
        db.execute('DELETE FROM transcription_results WHERE user_id=? AND id=?', (user_id, transcription_id))
    return {'deleted': True}


@lru_cache(maxsize=1)
def _load_local_model(directory: str):
    try:
        from faster_whisper import WhisperModel
        return WhisperModel(directory, device='cpu', compute_type='int8', cpu_threads=int(os.getenv('ASR_CPU_THREADS','4')), local_files_only=True)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise TranscriptionError('ASR_NOT_READY', '本地转写依赖或模型不可用，请先安装可选 ASR 依赖并显式准备 small 模型') from exc


def _load_model():
    directory = os.getenv('ASR_MODEL_DIR', '')
    if os.getenv('ASR_ENABLED', '').lower() != 'true' or not directory:
        raise TranscriptionError('ASR_NOT_READY', '本地转写未启用或未配置 ASR_MODEL_DIR，请先显式准备模型')
    model_path = Path(directory)
    if not all((model_path / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')):
        raise TranscriptionError('ASR_NOT_READY', '本地 small 模型文件不完整，请先运行 python -m backend.transcriber prepare-model')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise TranscriptionError('ASR_NOT_READY', '本地转写需要 ffmpeg 和 ffprobe，请安装后重试')
    return _load_local_model(str(model_path.resolve()))


def prepare_model() -> str:
    """只有此显式命令允许从公开 Hugging Face 仓库下载模型。"""
    directory = os.getenv('ASR_MODEL_DIR', '')
    if not directory:
        raise TranscriptionError('ASR_NOT_READY', '请先配置 ASR_MODEL_DIR，指定 small 模型保存目录')
    try:
        from faster_whisper import download_model
    except ImportError as exc:
        raise TranscriptionError('ASR_NOT_READY', '请先安装可选 ASR 依赖') from exc
    return download_model(MODEL_NAME, output_dir=directory)


def _stage(task_id: str, stage: str):
    tasks.ensure_task_active(task_id)
    with get_db() as db:
        db.execute("UPDATE tasks SET stage=?,version=version+1 WHERE id=? AND state='running' AND cancel_requested=0", (stage, task_id))


def _check_space():
    if tasks.queue_disk_bytes() >= tasks.MAX_QUEUE_BYTES:
        raise DownloadLimitExceeded('后台任务临时文件已达到 10 GiB，请稍后重试')


def _command(args: list[str], task_id: str):
    tasks.ensure_task_active(task_id)
    with get_db() as db:
        started = db.execute('SELECT started_at FROM tasks WHERE id=?', (task_id,)).fetchone()[0]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True, timeout=max(1, started + 1800 - time()))
    except subprocess.TimeoutExpired as exc:
        raise tasks.TaskStopped('转写超过处理期限') from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise TranscriptionError('AUDIO_UNAVAILABLE', '无法读取或转换音轨，请确认视频包含可用音频') from exc
    tasks.ensure_task_active(task_id)
    return result


def extract_audio(url: str, task_id: str) -> tuple[Path, int]:
    """下载只占用共享下载名额；ffprobe 校验实际音轨后转换为本地 WAV。"""
    downloader = VideoDownloader()
    downloader.download_dir = tasks.QUEUE_ROOT / task_id
    try:
        metadata = downloader._extract(url, {'skip_download': True,'extract_flat':'in_playlist','playlistend':1})
    except DownloadError as exc:
        raise TranscriptionError('AUDIO_UNAVAILABLE', '音轨信息获取失败，请检查视频是否可公开访问') from exc
    tasks.ensure_task_active(task_id)
    if metadata.get('is_live') or metadata.get('_type') in {'playlist', 'multi_video'}:
        raise TranscriptionError('AUDIO_UNSUPPORTED', '首版转写只支持普通单条视频，不支持直播或播放列表')
    duration = metadata.get('duration')
    if duration is not None:
        try:
            duration = float(duration)
        except (TypeError, ValueError) as exc:
            raise TranscriptionError('AUDIO_UNAVAILABLE', '平台返回了无效时长，请稍后重试') from exc
        if not math.isfinite(duration) or duration < 0:
            raise TranscriptionError('AUDIO_UNAVAILABLE', '平台返回了无效时长，请稍后重试')
        if duration > MAX_AUDIO_SECONDS:
            raise TranscriptionError('AUDIO_TOO_LONG', '单条转写最多支持 30 分钟')
    _check_space()

    def should_cancel():
        try:
            tasks.ensure_task_active(task_id)
        except tasks.TaskStopped:
            return True
        return False

    def progress(update):
        tasks.ensure_task_active(task_id)
        _check_space()

    _stage(task_id, 'downloading_audio')
    while not tasks.DOWNLOAD_SLOTS.acquire(timeout=0.2):
        tasks.ensure_task_active(task_id)
    try:
        tasks.ensure_task_active(task_id)
        downloaded = downloader.download(url, 'bestaudio/best', on_progress=progress, should_cancel=should_cancel, max_bytes=tasks.MAX_FILE_BYTES)
    finally:
        tasks.DOWNLOAD_SLOTS.release()
    path = Path(downloaded['filepath']).resolve()
    if not path.is_relative_to(downloader.download_dir.resolve()) or not path.is_file():
        raise TranscriptionError('AUDIO_UNAVAILABLE', '音轨未保存到当前任务目录')
    if path.stat().st_size > tasks.MAX_FILE_BYTES:
        raise DownloadLimitExceeded('音轨超过本地队列单文件大小限制')
    _check_space()
    _stage(task_id, 'checking_audio')
    probe = _command(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(path)], task_id)
    try:
        metadata = json.loads(probe.stdout)
        duration = float(metadata['format']['duration'])
        has_audio = any(stream['codec_type'] == 'audio' for stream in metadata['streams'])
        if not math.isfinite(duration) or duration <= 0 or not has_audio:
            raise ValueError('no usable audio')
    except (ValueError, KeyError, TypeError) as exc:
        raise TranscriptionError('AUDIO_UNAVAILABLE', '无法确定有效音轨的实际时长，本次未开始转写') from exc
    if duration > MAX_AUDIO_SECONDS:
        raise TranscriptionError('AUDIO_TOO_LONG', '单条转写最多支持 30 分钟，本次未开始转写')
    # 16 kHz 单声道 16 位 WAV 大小可预先计算，转换不能绕过临时目录预算。
    if tasks.queue_disk_bytes() + math.ceil(duration * 32000) + 4096 > tasks.MAX_QUEUE_BYTES:
        raise DownloadLimitExceeded('临时空间不足以转换本次音轨，请稍后重试')
    audio = downloader.download_dir / 'audio.wav'
    _command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(path), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-t', str(MAX_AUDIO_SECONDS + 1), str(audio)], task_id)
    _check_space()
    return audio, math.ceil(duration)


def transcribe(model, audio: Path, language: Language, task_id: str, audio_seconds: int) -> dict:
    """faster-whisper 在迭代分段时执行推理，每一段边界检查取消与预算。"""
    tasks.ensure_task_active(task_id)
    try:
        output, info = model.transcribe(str(audio), language=None if language == 'auto' else language, beam_size=5, vad_filter=True)
        segments = []
        for item in output:
            tasks.ensure_task_active(task_id)
            text = item.text.strip()
            start, end = float(item.start), float(item.end)
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or end > audio_seconds + 1:
                raise TranscriptionError('ASR_FAILED', '模型返回了无效时间戳，本次未保存字幕')
            if text:
                segments.append({'start': start, 'end': end, 'text': text})
        tasks.ensure_task_active(task_id)
        if not segments:
            raise TranscriptionError('NO_SPEECH', '音轨没有识别到可用语音，本次不计转写额度')
        return subtitle_result(segments, info.language, 'transcribed', 'transcription')
    except TranscriptionError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
        raise TranscriptionError('ASR_FAILED', '本地语音识别失败，本次未保存字幕或计额') from exc


def run_transcription_task(task_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=? AND kind='transcription'", (task_id,)).fetchone()
    if row is None or row['state'] not in {'running', 'cancelling'}:
        return
    data = json.loads(row['input_json'])
    reservation = None
    # 清理器必须知道线程仍可能写音频；取消落库后也不能提前删正在写的目录。
    with tasks._file_lock:
        tasks._executing.add(task_id)
    try:
        _stage(task_id, 'checking_subtitles')
        validate_public_url(data['url'])
        platform_subtitle = SubtitleExtractor().extract(data['url'])
        tasks.ensure_task_active(task_id)
        if platform_subtitle['has_subtitle']:
            raise TranscriptionError('SUBTITLE_AVAILABLE', '该视频已有平台字幕，请使用原字幕，无需语音转写')
        model = _load_model()
        tasks.ensure_task_active(task_id)
        audio, seconds = extract_audio(data['url'], task_id)
        tasks.ensure_task_active(task_id)
        reservation = extension_quota.reserve_extension_usage(row['user_id'], 'transcription', seconds, task_id)
        _stage(task_id, 'transcribing')
        subtitle = transcribe(model, audio, data['language'], task_id, seconds)
        with get_db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT state,cancel_requested,started_at FROM tasks WHERE id=?', (task_id,)).fetchone()
            if current is None or current['state'] != 'running' or current['cancel_requested'] or time() >= current['started_at'] + 1800:
                raise tasks.TaskStopped('任务已取消、中断或超过处理期限')
            extension_quota.commit_extension_usage(reservation.request_id, db)
            db.execute('''INSERT INTO transcription_results(id,user_id,url,result_json,audio_seconds,model_name,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,url) DO UPDATE SET result_json=excluded.result_json,
                audio_seconds=excluded.audio_seconds,model_name=excluded.model_name,updated_at=excluded.updated_at''',
                (secrets.token_urlsafe(24), row['user_id'], data['url'], json.dumps(subtitle, ensure_ascii=False), seconds, MODEL_NAME, int(time())))
            result_id = db.execute('SELECT id FROM transcription_results WHERE user_id=? AND url=?', (row['user_id'], data['url'])).fetchone()[0]
            source_hash = hashlib.sha256(json.dumps(subtitle, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            db.execute("DELETE FROM subtitle_translations WHERE user_id=? AND source_kind='transcription' AND source_id=? AND source_hash<>?", (row['user_id'], result_id, source_hash))
            db.execute("UPDATE tasks SET state='succeeded',stage='ready',result_id=?,finished_at=?,version=version+1 WHERE id=?", (result_id, int(time()), task_id))
    except (tasks.TaskStopped, DownloadCancelled) as exc:
        tasks.finish_task_failure(task_id, 'failed', 'TASK_TIMEOUT', str(exc))
    except TranscriptionError as exc:
        tasks.finish_task_failure(task_id, 'failed', exc.code, str(exc))
    except DownloadLimitExceeded as exc:
        tasks.finish_task_failure(task_id, 'failed', 'DOWNLOAD_LIMIT', str(exc))
    except VideoError as exc:
        tasks.finish_task_failure(task_id, 'failed', 'AUDIO_UNAVAILABLE', str(exc))
    except QuotaError as exc:
        tasks.finish_task_failure(task_id, 'failed', 'TASK_TIMEOUT' if exc.code == 'QUOTA_COMMIT_FAILED' else exc.code, str(exc))
    finally:
        if reservation:
            extension_quota.release_extension_usage(reservation.request_id)
        with tasks._file_lock:
            tasks._executing.discard(task_id)
        tasks._cleanup_file(task_id)


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent/'.env')
    parser = argparse.ArgumentParser(description='显式准备本地 small 语音识别模型')
    parser.add_argument('command', choices=['prepare-model'])
    parser.parse_args()
    print(prepare_model())
