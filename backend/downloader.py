"""视频格式和下载处理；第三方异常在这里转成可读提示。"""

import ipaddress
import os
import re
import shutil
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import yt_dlp
from yt_dlp.utils import DownloadError


class VideoError(ValueError):
    """可直接展示给用户的输入或上游错误。"""


class DownloadCancelled(VideoError):
    """后台下载已取消，不应交付文件。"""


class DownloadLimitExceeded(VideoError):
    """后台下载超过约定的单文件或空间限制。"""


def validate_public_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            raise VideoError("请输入有效的 HTTP 或 HTTPS 视频链接")
        if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local", ".internal")):
            raise VideoError("不支持本地或内网地址")
        try:
            addresses = [ipaddress.ip_address(host)]
            allow_fake_ip = False
        except ValueError:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)]
            allow_fake_ip = os.getenv("ALLOW_PROXY_FAKE_IP", "false").lower() == "true"
        fake_ip_range = ipaddress.ip_network("198.18.0.0/15")
        if not addresses or any(not address.is_global and not (allow_fake_ip and address in fake_ip_range) for address in addresses):
            raise VideoError("不支持本地或内网地址")
    except (socket.gaierror, ValueError) as exc:
        if isinstance(exc, VideoError):
            raise
        raise VideoError("链接地址无效或无法访问") from exc
    return url


def remote_get(url: str, *, headers: dict | None = None) -> httpx.Response:
    """字幕等远程资源每次跳转后都重新检查公网地址。"""
    for _ in range(6):
        validate_public_url(url)
        response = httpx.get(url, headers=headers, timeout=15, follow_redirects=False)
        if response.is_redirect:
            url = urljoin(url, response.headers["location"])
            continue
        response.raise_for_status()
        if len(response.content) > 8 * 1024 * 1024:
            raise VideoError("远程资源过大")
        return response
    raise VideoError("远程资源跳转过多")


YDL_OPTIONS = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 10, "retries": 1, "extractor_retries": 1}


class VideoDownloader:
    def __init__(self):
        self.download_dir = Path(os.getenv('DOWNLOAD_DIR', str(Path(__file__).parent / 'downloads')))
        self.ffmpeg_path = shutil.which("ffmpeg")

    def _extract(self, url: str, options: dict, download: bool = False) -> dict:
        from .douyin import DouyinParser, is_douyin_url

        if is_douyin_url(url):
            parser = DouyinParser()
            info = parser.download(url, options) if download else parser.parse(url, options)
        else:
            with yt_dlp.YoutubeDL({**YDL_OPTIONS, **options}) as ydl:
                info = ydl.extract_info(url, download=download)
        if not info or info.get("_type") in {"playlist", "multi_video"}:
            raise VideoError("请使用单个视频链接，暂不支持播放列表")
        return info

    def extract_info(self, url: str) -> dict:
        try:
            info = self._extract(url, {})
        except DownloadError as exc:
            raise VideoError("视频解析失败，请检查链接或稍后重试") from exc
        duration = int(info.get("duration") or 0)
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "id": info.get("id", ""), "title": info.get("title", "未命名视频"),
            "thumbnail": info.get("thumbnail", ""), "duration": info.get("duration"),
            "duration_string": f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}",
            "uploader": info.get("uploader") or info.get("channel") or "未知上传者",
            "platform": info.get("extractor_key") or info.get("extractor", "Unknown"),
            "view_count": info.get("view_count"), "upload_date": info.get("upload_date", ""),
            "description": (info.get("description") or "")[:500],
            "formats": self._formats(info), "subtitles": list(info.get("subtitles", {})),
            "automatic_captions": list(info.get("automatic_captions", {})),
        }

    def _formats(self, info: dict) -> list[dict]:
        raw = info.get("formats") or ([info] if info.get("url") else [])
        audio = [item for item in raw if item.get("vcodec") == "none" and item.get("acodec", "none") != "none"]
        results = []
        for item in raw:
            has_video = item.get("vcodec") != "none"
            has_audio = None if item.get("acodec") is None else item["acodec"] != "none"
            if not has_video and not has_audio:
                continue
            format_id = item.get("format_id", "best")
            ext = item.get("ext", "mp4")
            merged = has_video and has_audio is False and bool(audio)
            if merged:
                compatible = [track for track in audio if track.get("ext") == "m4a"] if ext == "mp4" else audio
                selected = (compatible or audio)[-1]
                format_id += "+" + selected["format_id"]
                has_audio = True
            height = item.get("height") or 0
            size = item.get("filesize") or item.get("filesize_approx")
            label = f"{height}p" if has_video and height else ("视频" if has_video else "仅音频")
            label += f" {ext.upper()}" + (" · 音视频合并" if merged else "")
            if has_video and has_audio is False:
                label += " · 无音频"
            if size and not merged:
                label += f" · {size / 1024 / 1024:.1f} MB"
            results.append({
                "format_id": format_id, "ext": ext, "resolution": item.get("resolution") or (f"{item.get('width', '?')}x{height}" if height else ("未知" if has_video else "audio")),
                "height": height, "filesize": None if merged else size, "filesize_approx": None if merged else size,
                "vcodec": item.get("vcodec"), "acodec": "merged" if merged else item.get("acodec"),
                "has_audio": has_audio, "has_video": has_video, "label": label,
            })
        return sorted(results, key=lambda item: (item["has_video"], item["height"], bool(item["has_audio"])), reverse=True)

    def get_url(self, url: str, format_id: str) -> dict:
        try:
            info = self._extract(url, {"format": format_id})
        except DownloadError as exc:
            raise VideoError("无法获取直链，请尝试服务端下载") from exc
        merged = bool(info.get("requested_formats"))
        direct_url = None if merged else info.get("url")
        protocol = info.get("protocol", "")
        custom_headers = {key.lower() for key in info.get("http_headers", {})} - {"user-agent", "accept", "accept-language", "accept-encoding"}
        requires_proxy = merged or bool(custom_headers) or protocol.startswith(("m3u8", "http_dash", "dash")) or not direct_url
        if direct_url:
            validate_public_url(direct_url)
        return {
            "direct_url": direct_url, "ext": info.get("ext", "mp4"),
            "filesize": info.get("filesize") or info.get("filesize_approx"), "title": info.get("title", "video"),
            "requires_proxy": requires_proxy,
            "reason": "需要合并音视频" if merged else ("需要服务端处理防盗链或分段视频" if requires_proxy else ""),
        }

    def cleanup_expired(self) -> None:
        if self.download_dir.exists():
            for path in self.download_dir.glob("task-*"):
                if path.is_dir() and time.time() - path.stat().st_mtime > 24 * 3600:
                    shutil.rmtree(path)

    def download(self, url: str, format_id: str, *, on_progress=None, should_cancel=None, max_bytes: int | None = None) -> dict:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        task_dir = Path(tempfile.mkdtemp(prefix="task-", dir=self.download_dir)).resolve()
        completed = False
        warning = ""
        options = {"format": format_id, "outtmpl": str(task_dir / "media.%(ext)s"), "windowsfilenames": True,
                   "skip_unavailable_fragments": False, "concurrent_fragment_downloads": 4, "noprogress": True}
        downloaded_tracks = {}
        def check_cancelled():
            if should_cancel is not None and should_cancel():
                raise DownloadCancelled('下载已取消')
        def progress(data):
            check_cancelled()
            track = data.get('filename', '')
            downloaded_tracks[track] = data.get('downloaded_bytes', 0)
            if max_bytes is not None and sum(downloaded_tracks.values()) > max_bytes:
                raise DownloadLimitExceeded('下载文件超过本地队列的单文件大小限制')
            if on_progress is not None:
                total = data.get('total_bytes') or data.get('total_bytes_estimate')
                downloaded = data.get('downloaded_bytes', 0)
                on_progress({'stage': 'downloading', 'downloaded_bytes': downloaded,
                    'total_bytes': total, 'total_is_estimate': not bool(data.get('total_bytes')) and bool(total),
                    'speed_bytes_per_second': data.get('speed'), 'eta_seconds': data.get('eta'),
                    'track_id': data.get('info_dict', {}).get('format_id') or Path(track).name,
                    'percent': min(100, 100 * downloaded / total) if total else None})
        def postprocess(data):
            check_cancelled()
            if on_progress is not None:
                on_progress({'stage': 'merging', 'downloaded_bytes': None, 'total_bytes': None,
                    'total_is_estimate': False, 'speed_bytes_per_second': None, 'eta_seconds': None,
                    'track_id': None, 'percent': None})
        if on_progress is not None or should_cancel is not None or max_bytes is not None:
            options.update(progress_hooks=[progress], postprocessor_hooks=[postprocess])
        if max_bytes is not None:
            options['max_filesize'] = max_bytes
            # 独立播放列表不受noplaylist保证：先只读单项身份，实际下载也不展开列表。
            options.update(extract_flat='in_playlist',playlistend=1)
            def check_background_info(info, *, incomplete=False):
                if info.get('is_live') or info.get('live_status') in {'is_live','is_upcoming'}:
                    raise VideoError('后台任务暂不支持直播，请使用普通单条视频链接')
                if incomplete:
                    return None
                streams = info.get('requested_formats') or [info]
                if any(item.get('section_start') or item.get('section_end') for item in [info, *streams]):
                    raise VideoError('此片段需要外部下载器，请改用原单条下载')
                if sum(item.get('filesize') or 0 for item in streams) > max_bytes:
                    raise DownloadLimitExceeded('下载文件超过本地队列的单文件大小限制')
                for stream in streams:
                    protocol = stream.get('protocol') or urlsplit(stream.get('url','')).scheme
                    if protocol not in {'http','https','m3u8_native','http_dash_segments'}:
                        raise VideoError('此视频需要无法持续监控的外部下载器，请改用原单条下载')
                    if protocol == 'm3u8_native':
                        from yt_dlp.downloader.hls import HlsFD
                        from yt_dlp.dependencies import Cryptodome
                        try:
                            manifest = stream.get('hls_media_playlist_data')
                            if not manifest:
                                response = remote_get(stream['url'],headers=stream.get('http_headers'))
                                manifest = response.text
                                stream['url'] = str(response.url)
                        except httpx.HTTPError as exc:
                            raise VideoError('无法读取视频媒体清单，请稍后重试') from exc
                        if not HlsFD.can_download(manifest,stream) or ('#EXT-X-KEY:METHOD=AES-128' in manifest and not Cryptodome.AES):
                            raise VideoError('此HLS需要外部下载器，请改用原单条下载')
                        # 固定已检查的清单，禁止原生HLS在下载中回退到外部ffmpeg。
                        stream['hls_media_playlist_data'] = manifest
                return None
            options['match_filter'] = check_background_info
        if self.ffmpeg_path:
            options.update(ffmpeg_location=self.ffmpeg_path, merge_output_format="mp4/mkv")
        elif "+" in format_id:
            options["format"] = "best[vcodec!=none][acodec!=none]"
            warning = "ffmpeg unavailable; downloaded available combined audio/video format"
        try:
            check_cancelled()
            if max_bytes is not None:
                preflight = self._extract(url,{'format':options['format'],'extract_flat':'in_playlist','playlistend':1},download=False)
                check_cancelled()
                check_background_info(preflight)
            info = self._extract(url, options, download=True)
            check_cancelled()
            if max_bytes is not None and (info.get('filesize') or 0) > max_bytes:
                raise DownloadLimitExceeded('下载文件超过本地队列的单文件大小限制')
            candidates = [path for path in task_dir.glob("media.*") if path.suffix not in {".part", ".ytdl", ".temp"} and not re.search(r"\.f[^.]+\.", path.name)]
            final_path = Path(info["filepath"]).resolve() if info.get("filepath") else (candidates[0] if len(candidates) == 1 else None)
            if not final_path or final_path.parent != task_dir or not final_path.is_file():
                raise VideoError("下载未生成完整文件，请重试")
            if max_bytes is not None and final_path.stat().st_size > max_bytes:
                raise DownloadLimitExceeded('下载文件超过本地队列的单文件大小限制')
            title = re.sub(r'[\\/*?:"<>|\r\n]', "_", info.get("title") or "video").strip(" .")[:100] or "video"
            completed = True
            return {"filepath": str(final_path), "filename": title + final_path.suffix, "task_dir": str(task_dir), "warning": warning}
        except DownloadError as exc:
            raise VideoError("视频下载失败，平台可能限制访问，请稍后重试") from exc
        except OSError as exc:
            raise VideoError("服务器无法保存下载文件") from exc
        finally:
            if not completed:
                shutil.rmtree(task_dir)
