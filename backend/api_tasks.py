"""任务只接受本人身份；领取响应结束时释放文件占用，支持重复领取和 Range。"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from . import tasks
from .auth import AuthenticatedUser, get_current_user
from .database import get_db

router = APIRouter(prefix="/api", tags=["后台任务"])


class DownloadItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    format_id: str = Field(default="bestvideo+bestaudio/best", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_+./=<>!\[\]()-]+$")

    @field_validator("url")
    @classmethod
    def original_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("请提供不含账号密码的 HTTP 视频原始链接")
        return value


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=200)
    items: list[DownloadItem] = Field(min_length=1, max_length=10)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=200)


class TaskFileResponse(FileResponse):
    def __init__(self, task_id: str, lease: dict):
        self.task_id = task_id
        headers = {"X-Download-Warning": lease["warning"]} if lease["warning"] else None
        super().__init__(lease["path"], filename=lease["filename"], media_type="application/octet-stream", headers=headers)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(tasks.release_task_file, self.task_id)


@router.post("/batches", status_code=202)
def create_batch(req: BatchRequest, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": tasks.create_batch(user.id, req.request_key, [item.model_dump() for item in req.items])}


@router.get("/batches/{id}")
def get_batch(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": tasks.get_batch(user.id, id)}


@router.get("/tasks")
def list_tasks(kind: tasks.TaskKind | None = None, state: tasks.TaskState | None = None,
               batch_id: str | None = Query(default=None, max_length=200), limit: int = Query(default=20, ge=1, le=100),
               cursor: str | None = Query(default=None, max_length=1024), user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": tasks.list_tasks(user.id, kind=kind, state=state, batch_id=batch_id, limit=limit, cursor=cursor)}


@router.get("/tasks/{id}")
def get_task(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": tasks.get_task(user.id, id)}


@router.get("/tasks/{id}/result")
def get_task_result(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    from . import subtitles, transcriber, history
    task = tasks.get_task(user.id,id)
    if task['state'] != 'succeeded' or task['kind'] == 'download':
        raise HTTPException(409, {'code':'TASK_RESULT_NOT_READY','error':'字幕结果尚未完成，视频文件请使用领取入口'})
    if task['kind'] == 'transcription':
        result = transcriber.get_transcription_by_id(user.id,task['result_id'])
        return {'success':True,'data':{'kind':task['kind'],'url':task['url'],'result':result}}
    # 译文校验与原文展示共用读快照，避免重新生成期间混用两个版本。
    with get_db() as db, db:
        db.execute('BEGIN')
        result = subtitles.get_translation_by_id(user.id,task['result_id'],db=db)
        source = subtitles.get_subtitle_source(user.id,result['source_kind'],result['source_id'],db=db)
        if result['source_kind'] == 'summary':
            detail = history.get_history_detail(user.id,'summary',result['source_id'],db=db)
            source.update(summary_result=detail['result'],video_title=detail['video_title'],language=detail['language'])
        else:
            source['transcription_result'] = transcriber.get_transcription_by_id(user.id,result['source_id'],db=db)
    return {'success':True,'data':{'kind':task['kind'],'url':task['url'],'result':result,'source':source}}


@router.post("/tasks/{id}/cancel")
def cancel_task(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": tasks.cancel_task(user.id, id)}


@router.post("/tasks/{id}/retry", status_code=202)
def retry_task(id: str, req: RetryRequest, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": tasks.retry_task(user.id, id, req.request_key)}


@router.get("/tasks/{id}/file")
def download_task_file(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return TaskFileResponse(id, tasks.acquire_task_file(user.id, id))
