"""翻译只接受本人保存的字幕 ID；排队阶段不预占额度。"""

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from . import subtitles
from .auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/subtitles", tags=["字幕翻译"])


class TranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=200)
    source_kind: subtitles.SourceKind
    source_id: str = Field(min_length=1, max_length=200)
    target_language: subtitles.TargetLanguage
    force: bool = False


@router.post("/translate", status_code=202)
def translate_subtitles(req: TranslationRequest, response: Response, user: AuthenticatedUser = Depends(get_current_user)):
    result = subtitles.submit_translation(user.id, req.source_kind, req.source_id, req.target_language, req.request_key, req.force)
    response.status_code = 200 if result["cached"] else 202
    return {"success": True, "data": result}


@router.get("/translation")
def get_translation(source_kind: subtitles.SourceKind, target_language: subtitles.TargetLanguage,
                    source_id: str = Query(min_length=1, max_length=200), user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": subtitles.get_translation(user.id, source_kind, source_id, target_language)}


@router.delete("/translations/{id}")
def delete_translation(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": subtitles.delete_translation(user.id, id)}
