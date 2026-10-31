"""同步生成器由 FastAPI 放在线程池中，字幕和模型请求不阻塞事件循环。"""

from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from .downloader import VideoError, validate_public_url
from .summarizer import SubtitleExtractor, VideoSummarizer

router = APIRouter(prefix="/api", tags=["AI 总结"])


class SummarizeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    language: str = Field(default="zh", min_length=1, max_length=20)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    question: str = Field(min_length=1, max_length=4000)
    subtitle_text: str = Field(default="", max_length=300000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


@router.post("/summarize", response_class=EventSourceResponse)
def summarize_video(req: SummarizeRequest) -> Iterator[ServerSentEvent]:
    try:
        validate_public_url(req.url)
        subtitle = SubtitleExtractor().extract(req.url)
        yield ServerSentEvent(event="subtitle", data=subtitle)
        if not subtitle["has_subtitle"]:
            raise VideoError("该视频没有可用字幕，暂不支持语音转文字；仍可下载视频")
        with VideoSummarizer() as summarizer:
            for token in summarizer.summarize_stream(subtitle["full_text"], req.language):
                yield ServerSentEvent(event="summary", data=token)
            markdown = summarizer.generate_mindmap(subtitle["full_text"], req.language)
            yield ServerSentEvent(event="mindmap", data={"markdown": markdown})
        yield ServerSentEvent(event="done", raw_data="[DONE]")
    except VideoError as exc:
        yield ServerSentEvent(event="error", data={"message": str(exc)})


@router.post("/chat", response_class=EventSourceResponse)
def chat_with_video(req: ChatRequest) -> Iterator[ServerSentEvent]:
    try:
        validate_public_url(req.url)
        text = req.subtitle_text.strip()
        if not text:
            subtitle = SubtitleExtractor().extract(req.url)
            if not subtitle["has_subtitle"]:
                raise VideoError("该视频没有可用字幕，无法基于视频回答问题")
            text = subtitle["full_text"]
        if not req.question.strip():
            raise VideoError("请输入问题")
        with VideoSummarizer() as summarizer:
            for token in summarizer.chat_stream(text, req.question, [message.model_dump() for message in req.history]):
                yield ServerSentEvent(event="answer", data=token)
        yield ServerSentEvent(event="done", raw_data="[DONE]")
    except VideoError as exc:
        yield ServerSentEvent(event="error", data={"message": str(exc)})
