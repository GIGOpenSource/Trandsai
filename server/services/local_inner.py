"""第 3 期：本地 Inner HTTP 客户端（D1）— 失败三级降级。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

LOCAL_INNER_BASE = (os.getenv("LOCAL_INNER_BASE") or "").rstrip("/")
LOCAL_INNER_TIMEOUT_S = float(os.getenv("LOCAL_INNER_TIMEOUT_S", "8"))
LOCAL_INNER_ENABLED = os.getenv("LOCAL_INNER_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)


def local_inner_available() -> bool:
    return bool(LOCAL_INNER_ENABLED and LOCAL_INNER_BASE)


def call_local_inner(payload: Dict[str, Any]) -> Optional[str]:
    """
    POST {LOCAL_INNER_BASE}/v1/inner
    期望返回 { "text": "思考:...\\n情绪:...\\n亲密度信号:...\\n反思:...\\n创意:..." }
    或纯文本。失败返回 None（由调用方回退外部 Inner / light_prep）。
    """
    if not local_inner_available():
        return None
    url = f"{LOCAL_INNER_BASE}/v1/inner"
    try:
        with httpx.Client(timeout=LOCAL_INNER_TIMEOUT_S) as client:
            resp = client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning("local inner HTTP %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
            if isinstance(data, dict):
                text = data.get("text") or data.get("content") or ""
                return str(text).strip() or None
            return (resp.text or "").strip() or None
    except Exception as e:
        logger.warning("local inner failed: %s", e)
        return None
