"""多马甲 / 多应用标识（X-App-Key）。

业务数据默认互通；统计与归因写入按 app_key 打标，管理端可筛。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# 正式马甲码表（写入 / 筛选白名单）
APP_KEY_TRANDAI = "trandsai"
APP_KEY_KAYA = "kaya"
DEFAULT_APP_KEY = APP_KEY_TRANDAI

KNOWN_APP_KEYS = (APP_KEY_TRANDAI, APP_KEY_KAYA)

APP_KEY_OPTIONS: List[Dict[str, str]] = [
    {"value": APP_KEY_TRANDAI, "label": "Trandsai"},
    {"value": APP_KEY_KAYA, "label": "KAYA"},
]

# Header 名（nginx 马甲域名会注入；客户端也可自带）
HEADER_APP_KEY = "X-App-Key"


def normalize_app_key(raw: Optional[str], *, default: str = DEFAULT_APP_KEY) -> str:
    """规范化 app_key；未知值回落 default（兼容老客户端不传）。"""
    key = (raw or "").strip().lower()
    if not key:
        return default
    # 常见别名
    aliases = {
        "trands": APP_KEY_TRANDAI,
        "trands-ai": APP_KEY_TRANDAI,
        "trands_ai": APP_KEY_TRANDAI,
        "kaya-app": APP_KEY_KAYA,
        "kaya_app": APP_KEY_KAYA,
    }
    key = aliases.get(key, key)
    if key in KNOWN_APP_KEYS:
        return key
    # 未登记马甲：截断后原样保留（便于后续扩码），最长 32
    return key[:32] or default


def resolve_app_key_filter(raw: Optional[str]) -> Optional[str]:
    """管理端筛选：空 / all / 全部 → None（不筛）；否则规范化。"""
    s = (raw or "").strip().lower()
    if not s or s in ("all", "全部", "*"):
        return None
    return normalize_app_key(s)


def resolve_stats_app_key(raw: Optional[str]) -> str:
    """数据统计专用：不传 / 空 / all / 全部 → trandsai（兼容已上线主包）；显式 kaya → kaya。

    历史埋点未打标视为 trandsai；kaya 必须显式上报并筛选。
    """
    s = (raw or "").strip().lower()
    if not s or s in ("all", "全部", "*", "default"):
        return DEFAULT_APP_KEY
    return normalize_app_key(s, default=DEFAULT_APP_KEY)


def app_key_meta() -> Dict[str, Any]:
    return {
        "header": HEADER_APP_KEY,
        "default": DEFAULT_APP_KEY,
        "apps": list(APP_KEY_OPTIONS),
        "param": "app_key",
        "note": "客户端请求带 X-App-Key；不传视为 trandsai。统计表写入 app_key，管理端可筛。",
    }


def app_key_from_headers(headers: Any) -> str:
    """从请求头解析马甲；兼容 X-App-Key / x-app-key。"""
    raw = None
    try:
        raw = headers.get(HEADER_APP_KEY) or headers.get("x-app-key")
    except Exception:
        raw = None
    return normalize_app_key(raw)


def _split_csv_ids(raw: Optional[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for part in (raw or "").split(","):
        item = part.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def apple_client_ids_for_app_key(app_key: Optional[str]) -> List[str]:
    """按马甲返回 Sign in with Apple 的 aud / client_id 列表。

    - trandsai：APPLE_BUNDLE_ID → APPLE_SERVICE_ID（与历史一致）
    - kaya：APPLE_CLIENT_IDS（逗号分隔），缺省时可设 APPLE_BUNDLE_ID_KAYA
    """
    key = normalize_app_key(app_key)
    if key == APP_KEY_KAYA:
        ids = _split_csv_ids(os.getenv("APPLE_CLIENT_IDS", ""))
        kaya_bundle = (os.getenv("APPLE_BUNDLE_ID_KAYA", "") or "").strip()
        if kaya_bundle and kaya_bundle not in ids:
            ids.insert(0, kaya_bundle)
        return ids

    bundle = (os.getenv("APPLE_BUNDLE_ID", "") or "").strip()
    service = (os.getenv("APPLE_SERVICE_ID", "") or "").strip()
    if not bundle:
        bundle = service or "com.gig.trandsai"
    if not service:
        service = bundle
    return _split_csv_ids(f"{bundle},{service}")
