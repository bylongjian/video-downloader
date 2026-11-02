import os
import time
from dataclasses import dataclass

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import database

security = HTTPBearer(auto_error=False)
JWT_EXPIRE_HOURS = 72


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    email: str
    session_id: str


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))


def create_token(user_id: int, session_id: str) -> str:
    session = database.get_session(session_id)
    return jwt.encode({"sub": str(user_id), "jti": session_id, "iat": session["created_at"], "exp": session["expires_at"]}, os.environ["JWT_SECRET"], algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"], options={"require": ["sub", "jti", "iat", "exp"]})
        payload["sub"] = int(payload["sub"])
        return payload
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise HTTPException(401, {"code": "SESSION_INVALID", "error": "登录已失效，请重新登录"}, headers={"WWW-Authenticate": "Bearer"}) from exc


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> AuthenticatedUser:
    if credentials is None:
        raise HTTPException(401, {"code": "AUTH_REQUIRED", "error": "请先登录后使用 AI 功能"}, headers={"WWW-Authenticate": "Bearer"})
    return authenticate_token(credentials.credentials)


def authenticate_token(token: str) -> AuthenticatedUser:
    payload = decode_token(token)
    session = database.get_session(payload["jti"])
    user = database.get_user_by_id(payload["sub"])
    if session is None or user is None or session["user_id"] != user["id"] or session["revoked_at"] is not None or session["expires_at"] <= time.time():
        raise HTTPException(401, {"code": "SESSION_INVALID", "error": "登录已失效，请重新登录"}, headers={"WWW-Authenticate": "Bearer"})
    return AuthenticatedUser(user["id"], user["email"], session["id"])
