"""显式转写入口；恢复成功结果没有下载或推理副作用。"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import transcriber
from .auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix='/api/transcriptions', tags=['语音转写'])


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=4096)
    language: transcriber.Language = 'auto'
    force: bool = False

    @field_validator('url')
    @classmethod
    def original_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError('请提供不含账号密码的 HTTP 视频原始链接')
        return value


@router.post('', status_code=202)
def create_transcription(req: TranscriptionRequest, response: Response, user: AuthenticatedUser = Depends(get_current_user)):
    result = transcriber.submit_transcription(user.id, req.url, req.language, req.request_key, req.force)
    response.status_code = 200 if result['cached'] else 202
    return {'success': True, 'data': result}


@router.get('/result')
def get_transcription(url: str = Query(min_length=1, max_length=4096), user: AuthenticatedUser = Depends(get_current_user)):
    return {'success': True, 'data': transcriber.get_transcription(user.id, url)}


@router.delete('/{id}')
def delete_transcription(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {'success': True, 'data': transcriber.delete_transcription(user.id, id)}
