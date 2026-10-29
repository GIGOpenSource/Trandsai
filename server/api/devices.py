"""设备推送 token 注册 / 注销。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core.auth import verify_token as redis_verify_token
from core.i18n import auth_expired_message, auth_required_message
from services.push_sender import disable_device_token, upsert_device_token

router = APIRouter(prefix="/api/devices", tags=["推送"])


class PushTokenBody(BaseModel):
    token: str = Field(..., min_length=8, max_length=512)
    platform: str = Field(default="android", description="android / ios")
    provider: str = Field(default="fcm", description="fcm / apns / unipush")
    device_id: str = Field(default="", max_length=64)
    app_version: str = Field(default="", max_length=32)


class PushTokenDisableBody(BaseModel):
    token: Optional[str] = None
    device_id: Optional[str] = None


def _require_user(request: Request, x_token: Optional[str]) -> int:
    al = request.headers.get("accept-language")
    if not x_token:
        raise HTTPException(status_code=401, detail=auth_required_message(al))
    # 与登录一致：Redis token（非 DB UserTokenORM）
    uid = redis_verify_token(x_token)
    if not uid:
        raise HTTPException(status_code=401, detail=auth_expired_message(al))
    return int(uid)


@router.post("/push-token", summary="注册/更新系统推送 token")
async def register_push_token(
    request: Request,
    body: PushTokenBody,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    user_id = _require_user(request, x_token)
    result = upsert_device_token(
        user_id=user_id,
        token=body.token,
        platform=body.platform,
        provider=body.provider,
        device_id=body.device_id,
        app_version=body.app_version,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "invalid"))
    return result


@router.delete("/push-token", summary="注销系统推送 token")
async def unregister_push_token(
    request: Request,
    body: PushTokenDisableBody,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    user_id = _require_user(request, x_token)
    n = disable_device_token(user_id=user_id, token=body.token, device_id=body.device_id)
    return {"ok": True, "disabled": n}
