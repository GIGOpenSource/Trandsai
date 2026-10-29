"""用户 Messages 未读长连接：启动即连，推总未读 + 当前列表各智能体未读。"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.auth import verify_token as verify_user_token, verify_ws_ticket
from core.i18n import _WS_AUTH_FAILED, normalize_ui_language
from services.user_chat_unread import (
    INBOX_HEARTBEAT_SEC,
    INBOX_IDLE_TIMEOUT_SEC,
    bind_inbox_loop,
    clear_user_chat_unread,
    hello_payload,
    parse_companion_ids,
    register_user_inbox,
    snapshot,
    unregister_user_inbox,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["聊天"])


def _auth_user_id(websocket: WebSocket):
    token = (
        websocket.headers.get("x-token")
        or websocket.headers.get("X-Token")
        or websocket.query_params.get("token")
    )
    user_id = verify_user_token(token) if token else None
    if user_id:
        return int(user_id)
    ticket = (websocket.query_params.get("ticket") or "").strip()
    if not ticket:
        return None
    uid = verify_ws_ticket(ticket)
    return int(uid) if uid else None


async def _send(websocket: WebSocket, payload: dict) -> bool:
    try:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


@router.websocket("/ws/inbox")
async def ws_user_inbox(websocket: WebSocket):
    """
    用户端长连接（App 启动即连）。断网后由客户端重连；服务端心跳探测死连接。
    未读按 user_id 隔离；打开某个聊天才清那一条，打开列表不清全部。
    """
    await websocket.accept()
    ui_lang = normalize_ui_language(websocket.query_params.get("lang"))
    user_id = _auth_user_id(websocket)
    if not user_id:
        try:
            err = _WS_AUTH_FAILED.get(ui_lang, _WS_AUTH_FAILED["en"])
            await _send(websocket, {"type": "error", "text": err})
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    bind_inbox_loop(asyncio.get_running_loop())
    await register_user_inbox(user_id, websocket)
    last_seen = time.monotonic()

    if not await _send(websocket, hello_payload(user_id)):
        await unregister_user_inbox(user_id, websocket)
        return

    boot_ids = parse_companion_ids(
        websocket.query_params.get("companion_ids") or websocket.query_params.get("ids")
    )
    if boot_ids:
        payload = snapshot(user_id, boot_ids)
        payload["type"] = "unread_list"
        await _send(websocket, payload)

    async def _keepalive():
        nonlocal last_seen
        while True:
            await asyncio.sleep(INBOX_HEARTBEAT_SEC)
            idle = time.monotonic() - last_seen
            if idle > INBOX_IDLE_TIMEOUT_SEC:
                try:
                    await websocket.close(code=1001)
                except Exception:
                    pass
                return
            if not await _send(websocket, {"type": "ping"}):
                return

    ka = asyncio.create_task(_keepalive(), name=f"inbox-ka-{user_id}")
    try:
        while True:
            raw = await websocket.receive_text()
            last_seen = time.monotonic()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            mtype = (data.get("type") or "").strip()
            if mtype in ("ping", "pong"):
                await _send(websocket, {"type": "pong"})
                continue
            if mtype == "watch":
                ids = parse_companion_ids(data.get("companion_ids") or data.get("ids"))
                payload = snapshot(user_id, ids)
                payload["type"] = "unread_list"
                await _send(websocket, payload)
                continue
            if mtype == "sync":
                await _send(websocket, hello_payload(user_id))
                continue
            if mtype == "read":
                cid = str(data.get("companion_id") or data.get("id") or "").strip()
                if not cid:
                    await _send(
                        websocket,
                        {
                            "type": "error",
                            "code": "read_requires_companion",
                            "text": "read 必须带 companion_id，不会一键清空全部未读",
                        },
                    )
                    continue
                info = clear_user_chat_unread(user_id, cid, notify=True)
                await _send(
                    websocket,
                    {
                        "type": "unread",
                        "user_id": int(user_id),
                        "companion_id": cid[:8],
                        "unread_count": 0,
                        "total_unread": int(info.get("total_unread") or 0),
                    },
                )
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_user_inbox error user_id=%s", user_id)
    finally:
        ka.cancel()
        try:
            await ka
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        await unregister_user_inbox(user_id, websocket)
