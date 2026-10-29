"""
操作系统级推送（O-21）：FCM（Android / 可选 iOS）+ APNs（iOS）+ UniPush clientId。

环境变量（未配置时 send 静默跳过，不影响端内通知）：
  PUSH_ENABLED=1
  FCM_PROJECT_ID=...
  FCM_SERVICE_ACCOUNT_JSON=/path/to/sa.json   # 或 FCM_SERVICE_ACCOUNT_JSON_INLINE='{...}'
  APNS_KEY_PATH=/path/to/AuthKey_XXX.p8
  APNS_KEY_ID=...
  APNS_TEAM_ID=...
  APNS_BUNDLE_ID=com.example.app
  APNS_USE_SANDBOX=1|0
  UNIPUSH_CLOUD_URL=   # 可选：DCloud 离线推送 HTTP 接口
  UNIPUSH_APP_ID=
  UNIPUSH_APP_KEY=
  UNIPUSH_MASTER_SECRET=
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from core.database import DevicePushTokenORM, get_db

logger = logging.getLogger(__name__)


def push_enabled() -> bool:
    return os.getenv("PUSH_ENABLED", "0").lower() in ("1", "true", "yes")


def upsert_device_token(
    *,
    user_id: int,
    token: str,
    platform: str = "android",
    provider: str = "fcm",
    device_id: str = "",
    app_version: str = "",
) -> dict:
    token = (token or "").strip()
    if not user_id or not token:
        return {"ok": False, "reason": "missing_token"}
    platform = (platform or "android").strip().lower()[:20]
    provider = (provider or "fcm").strip().lower()[:20]
    if platform not in ("android", "ios", "harmony"):
        platform = "android"
    if provider not in ("fcm", "apns", "unipush"):
        provider = "fcm"

    now = datetime.now(timezone.utc)
    with get_db() as db:
        row = (
            db.query(DevicePushTokenORM)
            .filter(
                DevicePushTokenORM.provider == provider,
                DevicePushTokenORM.token == token,
            )
            .first()
        )
        if row:
            row.user_id = user_id
            row.platform = platform
            row.device_id = (device_id or "")[:64]
            row.app_version = (app_version or "")[:32]
            row.enabled = True
            row.last_seen_at = now
            row.updated_at = now
        else:
            # 同用户同设备更新：按 device_id 收敛
            if device_id:
                old = (
                    db.query(DevicePushTokenORM)
                    .filter(
                        DevicePushTokenORM.user_id == user_id,
                        DevicePushTokenORM.device_id == device_id,
                        DevicePushTokenORM.provider == provider,
                    )
                    .first()
                )
                if old:
                    old.token = token
                    old.platform = platform
                    old.app_version = (app_version or "")[:32]
                    old.enabled = True
                    old.last_seen_at = now
                    old.updated_at = now
                    return {"ok": True, "id": old.id, "updated": True}

            row = DevicePushTokenORM(
                user_id=user_id,
                platform=platform,
                provider=provider,
                token=token,
                device_id=(device_id or "")[:64],
                app_version=(app_version or "")[:32],
                enabled=True,
                last_seen_at=now,
            )
            db.add(row)
            db.flush()
        return {"ok": True, "id": row.id}


def disable_device_token(*, user_id: int, token: Optional[str] = None, device_id: Optional[str] = None) -> int:
    with get_db() as db:
        q = db.query(DevicePushTokenORM).filter(DevicePushTokenORM.user_id == user_id)
        if token:
            q = q.filter(DevicePushTokenORM.token == token)
        if device_id:
            q = q.filter(DevicePushTokenORM.device_id == device_id)
        return q.update(
            {"enabled": False, "updated_at": datetime.now(timezone.utc)},
            synchronize_session=False,
        ) or 0


def list_user_tokens(user_id: int) -> list[DevicePushTokenORM]:
    with get_db() as db:
        return (
            db.query(DevicePushTokenORM)
            .filter(
                DevicePushTokenORM.user_id == user_id,
                DevicePushTokenORM.enabled.is_(True),
            )
            .all()
        )


def send_push_to_user(
    user_id: int,
    *,
    title: str,
    body: str,
    data: Optional[dict[str, Any]] = None,
) -> dict:
    """向用户所有启用设备发送系统推送。凭证未配置时跳过。"""
    if not push_enabled():
        return {"ok": False, "skipped": True, "reason": "push_disabled"}
    tokens = list_user_tokens(user_id)
    if not tokens:
        return {"ok": False, "skipped": True, "reason": "no_tokens"}

    sent = 0
    failed = 0
    details = []
    payload_data = {str(k): str(v) for k, v in (data or {}).items()}

    for row in tokens:
        try:
            if row.provider == "fcm":
                ok, info = _send_fcm(row.token, title, body, payload_data)
            elif row.provider == "apns":
                ok, info = _send_apns(row.token, title, body, payload_data)
            elif row.provider == "unipush":
                ok, info = _send_unipush(row.token, title, body, payload_data)
            else:
                ok, info = False, "unknown_provider"
            if ok:
                sent += 1
            else:
                failed += 1
                if info in ("not_registered", "invalid_token"):
                    _disable_token_id(row.id)
            details.append({"id": row.id, "provider": row.provider, "ok": ok, "info": info})
        except Exception as e:
            failed += 1
            details.append({"id": row.id, "provider": row.provider, "ok": False, "info": str(e)[:120]})
            logger.warning("[push] send failed user=%s token_id=%s: %s", user_id, row.id, e)

    logger.info(
        "push_sent user=%s title=%s sent=%s failed=%s",
        user_id,
        (title or "")[:40],
        sent,
        failed,
    )
    return {"ok": sent > 0, "sent": sent, "failed": failed, "details": details}


def _disable_token_id(token_id: int) -> None:
    try:
        with get_db() as db:
            row = db.query(DevicePushTokenORM).filter_by(id=token_id).first()
            if row:
                row.enabled = False
                row.updated_at = datetime.now(timezone.utc)
    except Exception:
        pass


# ---------- FCM HTTP v1 ----------

_fcm_access_token: Optional[str] = None
_fcm_access_expire: float = 0


def _load_fcm_credentials() -> Optional[dict]:
    inline = (os.getenv("FCM_SERVICE_ACCOUNT_JSON_INLINE") or "").strip()
    if inline:
        try:
            return json.loads(inline)
        except json.JSONDecodeError:
            logger.warning("[push] invalid FCM_SERVICE_ACCOUNT_JSON_INLINE")
            return None
    path = (os.getenv("FCM_SERVICE_ACCOUNT_JSON") or "").strip()
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _get_fcm_access_token() -> Optional[str]:
    global _fcm_access_token, _fcm_access_expire
    if _fcm_access_token and time.time() < _fcm_access_expire - 60:
        return _fcm_access_token
    creds = _load_fcm_credentials()
    if not creds:
        return None
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests

        scopes = ["https://www.googleapis.com/auth/firebase.messaging"]
        credentials = service_account.Credentials.from_service_account_info(creds, scopes=scopes)
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        _fcm_access_token = credentials.token
        _fcm_access_expire = time.time() + 3500
        return _fcm_access_token
    except Exception as e:
        logger.warning("[push] FCM auth failed: %s", e)
        return None


def _send_fcm(token: str, title: str, body: str, data: dict) -> tuple[bool, str]:
    project_id = (os.getenv("FCM_PROJECT_ID") or "").strip()
    access = _get_fcm_access_token()
    if not project_id or not access:
        return False, "fcm_not_configured"
    import urllib.request

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    message = {
        "message": {
            "token": token,
            "notification": {"title": title[:200], "body": (body or "")[:500]},
            "data": data,
            "android": {
                "priority": "HIGH",
                "notification": {"channel_id": "trandsai_default", "sound": "default"},
            },
            "apns": {
                "payload": {"aps": {"sound": "default", "badge": 1}},
            },
        }
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(message).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
            return True, "ok"
    except Exception as e:
        err = str(e)
        if "UNREGISTERED" in err or "404" in err:
            return False, "not_registered"
        return False, err[:160]


# ---------- APNs HTTP/2 (PyJWT + httpx optional) ----------

def _send_apns(token: str, title: str, body: str, data: dict) -> tuple[bool, str]:
    key_path = (os.getenv("APNS_KEY_PATH") or "").strip()
    key_id = (os.getenv("APNS_KEY_ID") or "").strip()
    team_id = (os.getenv("APNS_TEAM_ID") or "").strip()
    bundle_id = (os.getenv("APNS_BUNDLE_ID") or "").strip()
    if not all([key_path, key_id, team_id, bundle_id]) or not os.path.isfile(key_path):
        return False, "apns_not_configured"
    try:
        import jwt  # PyJWT
        import httpx
    except ImportError:
        return False, "apns_deps_missing"  # pip install PyJWT httpx

    with open(key_path, "r", encoding="utf-8") as f:
        key = f.read()
    now = int(time.time())
    jwt_token = jwt.encode(
        {"iss": team_id, "iat": now},
        key,
        algorithm="ES256",
        headers={"alg": "ES256", "kid": key_id},
    )
    host = (
        "https://api.sandbox.push.apple.com"
        if os.getenv("APNS_USE_SANDBOX", "1").lower() in ("1", "true", "yes")
        else "https://api.push.apple.com"
    )
    payload = {
        "aps": {
            "alert": {"title": title[:200], "body": (body or "")[:500]},
            "sound": "default",
            "badge": 1,
        },
        **data,
    }
    url = f"{host}/3/device/{token}"
    headers = {
        "authorization": f"bearer {jwt_token}",
        "apns-topic": bundle_id,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "content-type": "application/json",
    }
    try:
        with httpx.Client(http2=True, timeout=15.0) as client:
            resp = client.post(url, headers=headers, content=json.dumps(payload))
            if resp.status_code == 200:
                return True, "ok"
            if resp.status_code in (410, 400):
                return False, "invalid_token"
            return False, f"http_{resp.status_code}:{resp.text[:80]}"
    except Exception as e:
        return False, str(e)[:160]


# ---------- UniPush / DCloud 离线推送（可选） ----------

def _send_unipush(client_id: str, title: str, body: str, data: dict) -> tuple[bool, str]:
    """
    占位：若配置了 UNIPUSH_CLOUD_URL，则 POST JSON。
    具体字段以 DCloud / 个推离线推送文档为准；未配置则跳过。
    """
    url = (os.getenv("UNIPUSH_CLOUD_URL") or "").strip()
    if not url:
        return False, "unipush_not_configured"
    import urllib.request

    payload = {
        "cids": [client_id],
        "title": title[:200],
        "content": (body or "")[:500],
        "payload": data,
        "appId": os.getenv("UNIPUSH_APP_ID", ""),
        "appKey": os.getenv("UNIPUSH_APP_KEY", ""),
        "masterSecret": os.getenv("UNIPUSH_MASTER_SECRET", ""),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
            return True, "ok"
    except Exception as e:
        return False, str(e)[:160]
