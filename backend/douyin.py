"""抖音入口独立保留，解析和下载使用持续更新的 yt-dlp 提取器。"""

from urllib.parse import urlsplit

import yt_dlp


def is_douyin_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in {"douyin.com", "iesdouyin.com"} or host.endswith((".douyin.com", ".iesdouyin.com"))


class DouyinParser:
    def parse(self, url: str, options: dict) -> dict:
        from .downloader import YDL_OPTIONS

        with yt_dlp.YoutubeDL({**YDL_OPTIONS, **options}) as ydl:
            return ydl.extract_info(url, download=False)

    def download(self, url: str, options: dict) -> dict:
        from .downloader import YDL_OPTIONS

        with yt_dlp.YoutubeDL({**YDL_OPTIONS, **options}) as ydl:
            return ydl.extract_info(url, download=True)
