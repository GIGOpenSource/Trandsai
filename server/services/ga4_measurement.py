"""GA4 Measurement Protocol：归因接口收到 gclid 时服务端补报一份。

控制台配置（Firebase / GA4）：
1. Firebase Console → 项目设置 → 集成 → Google Analytics（确认已关联）
2. GA4 管理 → 数据流 → 选中 App/Web 流 → 测量 ID（G-XXXXXXXX）
3. 同一数据流 → Measurement Protocol API secrets → 创建 → 复制密钥

环境变量：
- GA4_MEASUREMENT_ID=G-XXXXXXXX（必填）
- GA4_API_SECRET=xxxx（或兼容别名 firebase_ads_key）
- GA4_MP_DEBUG=1  可选，走 debug 校验端点（不计入正式报表）
- GA4_MP_ENABLED=1  默认开启（配齐 ID+Secret 才真正发送）

前端建议在 attribution body 带上 Firebase Analytics 的 app_instance_id
（Android: FirebaseAnalytics.getAppInstanceId；iOS: Analytics.appInstanceID），
否则用 device_id 兜底，可能无法与 SDK 会话合并。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_EVENT_NAME_MAP = {
    "install": "server_install",
    "first_open": "server_first_open",
    "register": "sign_up",
    "login": "login",
    "purchase": "purchase",
}


def _ga4_measurement_id() -> str:
    return (os.getenv("GA4_MEASUREMENT_ID") or "").strip()


def _ga4_api_secret() -> str:
    # 兼容用户自定义名 firebase_ads_key
    return (
        (os.getenv("GA4_API_SECRET") or "").strip()
        or (os.getenv("firebase_ads_key") or "").strip()
        or (os.getenv("FIREBASE_ADS_KEY") or "").strip()
    )


def ga4_mp_configured() -> bool:
    return bool(_ga4_measurement_id() and _ga4_api_secret())


def _mp_enabled() -> bool:
    raw = (os.getenv("GA4_MP_ENABLED") or "1").strip().lower()
    if raw in ("0", "false", "off", "no", "disabled"):
        return False
    return ga4_mp_configured()


def _collect_url() -> str:
    mid = _ga4_measurement_id()
    secret = _ga4_api_secret()
    debug = (os.getenv("GA4_MP_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")
    base = (
        "https://www.google-analytics.com/debug/mp/collect"
        if debug
        else "https://www.google-analytics.com/mp/collect"
    )
    return f"{base}?measurement_id={mid}&api_secret={secret}"


def _event_name(event_type: str) -> str:
    et = (event_type or "install").strip().lower()
    return _EVENT_NAME_MAP.get(et, "server_attribution")


def _pick_app_instance_id(fields: Dict[str, Any]) -> str:
    raw = fields.get("raw_payload") if isinstance(fields.get("raw_payload"), dict) else {}
    for key in (
        "app_instance_id",
        "firebase_app_instance_id",
        "firebase_instance_id",
    ):
        val = str(fields.get(key) or raw.get(key) or "").strip()
        if val:
            return val[:64]
    return str(fields.get("device_id") or "").strip()[:64]


def build_mp_payload(fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """组装 Measurement Protocol JSON；无 gclid 则返回 None。"""
    gclid = str(fields.get("gclid") or "").strip()
    if not gclid:
        return None

    app_instance_id = _pick_app_instance_id(fields)
    device_id = str(fields.get("device_id") or "").strip()
    if not app_instance_id and not device_id:
        return None

    event_type = str(fields.get("event_type") or "install").strip().lower()
    params: Dict[str, Any] = {
        "gclid": gclid[:256],
        "engagement_time_msec": 1,
        "attribution_event_type": event_type,
    }
    for src_key, dst_key in (
        ("utm_source", "source"),
        ("utm_medium", "medium"),
        ("utm_campaign", "campaign"),
        ("utm_content", "content"),
        ("utm_term", "term"),
        ("install_source", "install_source"),
        ("os", "operating_system"),
        ("app_version", "app_version"),
    ):
        val = str(fields.get(src_key) or "").strip()
        if val:
            params[dst_key] = val[:100]

    # GA4 推荐的会话归因补充事件参数
    if params.get("source") or params.get("medium") or params.get("campaign"):
        params.setdefault("campaign_id", str(fields.get("utm_campaign") or "")[:100])

    payload: Dict[str, Any] = {
        "timestamp_micros": int(time.time() * 1_000_000),
        "non_personalized_ads": False,
        "events": [
            {
                "name": _event_name(event_type),
                "params": params,
            }
        ],
    }

    # App 流优先 app_instance_id；同时带 client_id 兼容 Web 流
    if app_instance_id:
        payload["app_instance_id"] = app_instance_id
    payload["client_id"] = device_id or app_instance_id or str(uuid4())

    user_id = fields.get("user_id")
    if user_id not in (None, ""):
        try:
            payload["user_id"] = str(int(user_id))
        except (TypeError, ValueError):
            payload["user_id"] = str(user_id)[:256]

    return payload


def send_gclid_to_ga4(fields: Dict[str, Any]) -> Dict[str, Any]:
    """同步发送；失败只记日志，不抛到业务主路径。"""
    if not _mp_enabled():
        return {"ok": False, "skipped": "not_configured_or_disabled"}

    body = build_mp_payload(fields)
    if not body:
        return {"ok": False, "skipped": "no_gclid_or_id"}

    url = _collect_url()
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = getattr(resp, "status", 200) or 200
            resp_body = resp.read().decode("utf-8", errors="replace")
        logger.info(
            "[GA4 MP] sent event=%s gclid=%s status=%s",
            (body.get("events") or [{}])[0].get("name"),
            str(fields.get("gclid") or "")[:16],
            status,
        )
        if (os.getenv("GA4_MP_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on") and resp_body:
            logger.info("[GA4 MP] debug response: %s", resp_body[:2000])
        return {"ok": True, "status": status, "debug": resp_body[:500] if resp_body else ""}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        logger.warning("[GA4 MP] HTTP %s: %s", e.code, err[:500])
        return {"ok": False, "error": f"http_{e.code}", "detail": err[:300]}
    except Exception as e:
        logger.warning("[GA4 MP] send failed: %s", e, exc_info=True)
        return {"ok": False, "error": str(e)[:200]}


def report_gclid_to_ga4_safe(fields: Dict[str, Any]) -> None:
    """供 BackgroundTasks / 线程调用：吞掉全部异常。"""
    try:
        if not str(fields.get("gclid") or "").strip():
            return
        send_gclid_to_ga4(fields)
    except Exception:
        logger.warning("[GA4 MP] report_gclid_to_ga4_safe failed", exc_info=True)
