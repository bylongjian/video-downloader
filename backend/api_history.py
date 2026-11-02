"""历史接口只访问当前会话本人数据。"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import history
from .auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/history", tags=["个人历史"])


class DownloadHistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    video_title: str = Field(default="", max_length=300)
    platform: str = Field(default="", max_length=100)
    format_id: str = Field(default="bestvideo+bestaudio/best", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_+./=<>!\[\]()-]+$")

    @field_validator("url")
    @classmethod
    def original_video_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("请提供不含账号密码的 HTTP 视频原始链接")
        return value


class DownloadEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: history.DownloadEvent


@router.get("")
def list_history(kind: history.HistoryKind = Query(), limit: int = Query(default=20, ge=1, le=100), cursor: str | None = Query(default=None, max_length=1024), user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": history.list_history(user.id, kind, limit, cursor)}


@router.post("/downloads", status_code=201)
def create_download_history(req: DownloadHistoryRequest, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": history.create_download_history(user.id, **req.model_dump())}


@router.post("/downloads/{id}/events")
def record_download_event(id: str, req: DownloadEventRequest, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": history.record_download_event(user.id, id, req.event)}


@router.get("/{kind}/{id}")
def get_history_detail(kind: history.HistoryKind, id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": history.get_history_detail(user.id, kind, id)}


@router.delete("/{kind}/{id}")
def delete_history(kind: history.HistoryKind, id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": history.delete_history(user.id, kind, id)}
