"""同步生成器由 FastAPI 放在线程池中，字幕和模型请求不阻塞事件循环。"""

from collections.abc import Iterator
from contextlib import ExitStack
from typing import Literal
import logging
import sqlite3

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from .downloader import VideoError, validate_public_url
from .summarizer import SubtitleExtractor, VideoSummarizer
from .quota import UsageReservation, QuotaError, commit_usage, ensure_active, reserve_summary_usage, reserve_chat_usage
from .auth import AuthenticatedUser, get_current_user
from .database import get_summary_result as load_summary_result

router = APIRouter(prefix="/api", tags=["AI 总结"])


def stream_resources() -> Iterator[ExitStack]:
    # FastAPI 的同步 SSE 生成器在断开后可能暂留；请求退出时明确关闭模型和流。
    with ExitStack() as resources:
        yield resources


class SummarizeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    language: str = Field(default="zh", min_length=1, max_length=20)
    video_title: str = Field(default='', max_length=300)
    platform: str = Field(default='', max_length=100)
    transcript_id: str | None = Field(default=None,min_length=1,max_length=200)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    question: str = Field(min_length=1, max_length=4000)
    subtitle_text: str = Field(default="", max_length=300000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


@router.get('/summarize/result')
def get_summary_result(url: str = Query(min_length=1, max_length=4096), language: str = Query(default='zh', min_length=1, max_length=20), user: AuthenticatedUser = Depends(get_current_user)):
    return {'success': True, 'data': load_summary_result(user.id, url, language)}


@router.post("/summarize", response_class=EventSourceResponse)
def summarize_video(req: SummarizeRequest, reservation: UsageReservation = Depends(reserve_summary_usage, scope='request'), resources: ExitStack = Depends(stream_resources, scope='request')) -> Iterator[ServerSentEvent]:
    try:
        ensure_active(reservation)
        validate_public_url(req.url)
        if req.transcript_id:
            from .transcriber import get_transcription_by_id
            saved = get_transcription_by_id(reservation.user_id,req.transcript_id)
            if saved['url'] != req.url:
                raise HTTPException(404,{'code':'SOURCE_NOT_FOUND','error':'转写来源不存在或无权访问'})
            subtitle = saved['subtitle']
        else:
            subtitle = SubtitleExtractor().extract(req.url)
        ensure_active(reservation)
        yield ServerSentEvent(event="subtitle", data=subtitle)
        if not subtitle["has_subtitle"]:
            yield ServerSentEvent(event='error', data={'code': 'NO_SUBTITLE', 'message': '该视频没有可用字幕；可在字幕页主动转写音频，完成后再生成总结，仍可直接下载视频'})
            return
        summarizer = resources.enter_context(VideoSummarizer())
        tokens = summarizer.summarize_stream(subtitle["full_text"], req.language)
        resources.callback(tokens.close)
        parts = []
        for token in tokens:
            ensure_active(reservation)
            parts.append(token)
            yield ServerSentEvent(event="summary", data=token)
        ensure_active(reservation)
        markdown = summarizer.generate_mindmap(subtitle["full_text"], req.language)
        ensure_active(reservation)
        yield ServerSentEvent(event="mindmap", data={"markdown": markdown})
        ensure_active(reservation)
        snapshot = commit_usage(reservation.request_id, summary_result={'url': req.url, 'language': req.language, 'subtitle': subtitle, 'summary': ''.join(parts), 'mindmap': markdown, 'video_title': req.video_title, 'platform': req.platform})
        yield ServerSentEvent(event='quota', data=snapshot)
        yield ServerSentEvent(event="done", raw_data="[DONE]")
    except VideoError as exc:
        yield ServerSentEvent(event="error", data={"code": 'AI_FAILED', "message": str(exc)})
    except HTTPException as exc:
        yield ServerSentEvent(event='error',data={'code':exc.detail['code'],'message':exc.detail['error']})
    except QuotaError as exc:
        yield ServerSentEvent(event='error', data={'code': exc.code, 'message': str(exc)})
    except sqlite3.OperationalError as exc:
        logging.getLogger(__name__).error('总结额度结算失败', exc_info=exc)
        yield ServerSentEvent(event='error', data={'code': 'QUOTA_COMMIT_FAILED', 'message': '额度结算暂不可用，请刷新额度后重试'})


@router.post("/chat", response_class=EventSourceResponse)
def chat_with_video(req: ChatRequest, reservation: UsageReservation = Depends(reserve_chat_usage, scope='request'), resources: ExitStack = Depends(stream_resources, scope='request')) -> Iterator[ServerSentEvent]:
    try:
        ensure_active(reservation)
        validate_public_url(req.url)
        text = req.subtitle_text.strip()
        if not text:
            subtitle = SubtitleExtractor().extract(req.url)
            ensure_active(reservation)
            if not subtitle["has_subtitle"]:
                yield ServerSentEvent(event='error', data={'code': 'NO_SUBTITLE', 'message': '该视频没有可用字幕，无法基于视频回答问题'})
                return
            text = subtitle["full_text"]
        if not req.question.strip():
            raise VideoError("请输入问题")
        ensure_active(reservation)
        summarizer = resources.enter_context(VideoSummarizer())
        tokens = summarizer.chat_stream(text, req.question, [message.model_dump() for message in req.history])
        resources.callback(tokens.close)
        for token in tokens:
            ensure_active(reservation)
            yield ServerSentEvent(event="answer", data=token)
        ensure_active(reservation)
        snapshot = commit_usage(reservation.request_id)
        yield ServerSentEvent(event='quota', data=snapshot)
        yield ServerSentEvent(event="done", raw_data="[DONE]")
    except VideoError as exc:
        yield ServerSentEvent(event="error", data={"code": 'AI_FAILED', "message": str(exc)})
    except QuotaError as exc:
        yield ServerSentEvent(event='error', data={'code': exc.code, 'message': str(exc)})
    except sqlite3.OperationalError as exc:
        logging.getLogger(__name__).error('问答额度结算失败', exc_info=exc)
        yield ServerSentEvent(event='error', data={'code': 'QUOTA_COMMIT_FAILED', 'message': '额度结算暂不可用，请刷新额度后重试'})
