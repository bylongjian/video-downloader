import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator

from . import database
from . import quota as quota_service
from .auth import AuthenticatedUser, JWT_EXPIRE_HOURS, create_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["账号"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=50)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码 UTF-8 编码不能超过 72 字节，请使用较短密码")
        return value


class LoginRequest(RegisterRequest):
    pass


def session_response(user: dict) -> dict:
    session_id = database.create_session(user["id"], int(time.time()) + JWT_EXPIRE_HOURS * 3600)
    return {"success": True, "data": {"token": create_token(user["id"], session_id), "token_type": "bearer", "user": {"id": user["id"], "email": user["email"]}, "quota": quota_service.get_quota(user["id"])}}


@router.post("/register", status_code=201)
def register(req: RegisterRequest):
    try:
        user = database.create_user(str(req.email), hash_password(req.password))
    except sqlite3.IntegrityError as exc:
        if exc.sqlite_errorname != "SQLITE_CONSTRAINT_UNIQUE":
            raise
        raise HTTPException(409, {"code": "EMAIL_EXISTS", "error": "该邮箱已注册，请切换到登录"}) from exc
    return session_response(user)


@router.post("/login")
def login(req: LoginRequest):
    user = database.get_user_by_email(str(req.email))
    if user is None or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, {"code": "INVALID_CREDENTIALS", "error": "邮箱或密码错误"})
    return session_response(user)


@router.get("/me")
def get_me(user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": {"user": {"id": user.id, "email": user.email}, "quota": quota_service.get_quota(user.id)}}


@router.get("/quota")
def get_quota(user: AuthenticatedUser = Depends(get_current_user)):
    return {"success": True, "data": quota_service.get_quota(user.id)}


@router.post("/logout")
def logout(user: AuthenticatedUser = Depends(get_current_user)):
    database.revoke_session(user.session_id)
    return {"success": True, "data": {"logged_out": True}}
