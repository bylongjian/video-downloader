"""连接只通知 SQLite 中的最新任务快照，不另存任务状态。"""

import asyncio
import json
import os
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .auth import authenticate_token
from .tasks import TERMINAL_STATES, get_task


router = APIRouter()
AUTH_TIMEOUT_SECONDS = 5
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15


@router.websocket("/api/tasks/{task_id}/progress")
async def task_progress(websocket: WebSocket, task_id: str):
    allowed_origins = {
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if origin.strip() not in ("", "*")
    }
    if websocket.headers.get("origin") not in allowed_origins or websocket.query_params:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        try:
            frame = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT_SECONDS)
        except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError, KeyError):
            await websocket.close(code=1008)
            return
        if not isinstance(frame, dict) or frame.get("type") != "authenticate" or not isinstance(frame.get("token"), str) or not frame["token"]:
            await websocket.close(code=1008)
            return

        token = frame["token"]
        user = await asyncio.to_thread(authenticate_token, token)
        snapshot = await asyncio.to_thread(get_task, user.id, task_id)
        await websocket.send_json({"type": "snapshot", "task": snapshot})
        version = snapshot["version"]
        if snapshot["state"] in TERMINAL_STATES:
            await websocket.close(code=1000)
            return

        poll_at = time.monotonic() + POLL_SECONDS
        heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS
        while True:
            # 等待期间也接收断线，避免客户端离开后留下轮询连接。
            try:
                timeout = max(0, min(poll_at, heartbeat_at) - time.monotonic())
                message = await asyncio.wait_for(websocket.receive(), timeout=timeout)
                if message["type"] == "websocket.disconnect":
                    return
            except TimeoutError:
                pass

            if time.monotonic() >= heartbeat_at:
                await asyncio.to_thread(authenticate_token, token)
                await websocket.send_json({"type": "heartbeat"})
                heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS

            if time.monotonic() < poll_at:
                continue
            poll_at = time.monotonic() + POLL_SECONDS
            snapshot = await asyncio.to_thread(get_task, user.id, task_id)
            if snapshot["version"] != version:
                await websocket.send_json({"type": "update", "task": snapshot})
                version = snapshot["version"]
                if snapshot["state"] in TERMINAL_STATES:
                    await websocket.close(code=1000)
                    return
    except HTTPException:
        # 不存在、越权和失效会话使用同一关闭方式，不发送任务或错误详情。
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        return
