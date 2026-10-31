"""运行入口：python -m uvicorn backend.main:app --reload。"""

import asyncio
import os
import shutil
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urljoin

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .downloader import VideoDownloader, VideoError, validate_public_url

load_dotenv(Path(__file__).parent / ".env")
downloader = VideoDownloader()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def cleanup():
        while True:
            await run_in_threadpool(downloader.cleanup_expired)
            await asyncio.sleep(3600)
    task = asyncio.create_task(cleanup())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="SaveAny 视频下载 API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","), allow_methods=["GET", "POST"], allow_headers=["Content-Type"], expose_headers=["Content-Disposition", "X-Download-Warning"])


class ParseRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)


class DownloadRequest(ParseRequest):
    format_id: str = Field(default="bestvideo+bestaudio/best", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_+./=<>!\[\]()-]+$")


@app.exception_handler(VideoError)
async def video_error_handler(request, exc: VideoError):
    return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"success": False, "error": "请求参数不正确，请检查链接或所选格式"})


@app.get("/api/health")
def health():
    return {"success": True, "data": {"status": "ok", "ffmpeg_available": bool(downloader.ffmpeg_path), "ai_configured": bool(os.getenv("DEEPSEEK_API_KEY")), "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash")}}


@app.post("/api/parse")
def parse_video(req: ParseRequest):
    validate_public_url(req.url)
    return {"success": True, "data": downloader.extract_info(req.url)}


@app.post("/api/direct-url")
def get_direct_url(req: DownloadRequest):
    validate_public_url(req.url)
    return {"success": True, "data": downloader.get_url(req.url, req.format_id)}


@app.post("/api/download")
def download_video(req: DownloadRequest):
    validate_public_url(req.url)
    result = downloader.download(req.url, req.format_id)
    headers = {"X-Download-Warning": result["warning"]} if result["warning"] else {}
    return FileResponse(result["filepath"], filename=result["filename"], headers=headers, background=BackgroundTask(shutil.rmtree, result["task_dir"]))


@app.get("/api/proxy/thumbnail")
def proxy_thumbnail(url: str):
    try:
        for _ in range(6):
            validate_public_url(url)
            with httpx.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, follow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}:
                    raise VideoError("远程资源不是支持的图片格式")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise VideoError("封面图片超过 2 MB")
                return Response(bytes(data), media_type=content_type, headers={"Cache-Control": "public, max-age=1800", "X-Content-Type-Options": "nosniff"})
        raise VideoError("封面图片跳转过多")
    except httpx.HTTPError as exc:
        raise VideoError("封面图片暂时无法访问") from exc


from .api_summarize import router

app.include_router(router)
