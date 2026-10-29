"""从 HTTP 请求提取客户端 IP / 设备 / 增长环境信息。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request

# moment_comments.user_device_id 等列为 VARCHAR(64)
_DEVICE_ID_MAX_LEN = 64


def normalize_device_id(raw: Optional[str], *, max_len: int = _DEVICE_ID_MAX_LEN) -> str:
    """规范化设备 ID。

    客户端若重复设置 X-Device-Id，代理/ASGI 常拼成
    ``uuid, uuid``（约 74 字符），写入 VARCHAR(64) 会报错。
    取逗号分隔的第一段并截断到 max_len。
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if "," in value:
        value = value.split(",", 1)[0].strip()
    if len(value) > max_len:
        value = value[:max_len]
    return value


def extract_client_ip(request: Request) -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        first = xff.split(",")[0].strip()
        if first and first.lower() != "unknown":
            return first
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return str(request.client.host)
    return ""


def extract_client_device(request: Request, x_device_id: Optional[str] = None) -> str:
    raw = (
        (x_device_id or "").strip()
        or (request.headers.get("X-Device-Id") or "").strip()
        or (request.headers.get("X-Device-ID") or "").strip()
        or (request.headers.get("X-Client-Device") or "").strip()
        or (request.headers.get("User-Agent") or "").strip()
    )
    return normalize_device_id(raw)


def _header(request: Request, *names: str) -> str:
    for name in names:
        value = (request.headers.get(name) or "").strip()
        if value:
            return value
    return ""


def extract_growth_headers(request: Request, x_device_id: Optional[str] = None) -> Dict[str, str]:
    """从标准 header 读取增长 / 归因环境（前端可选传）。"""
    accept_lang = _header(request, "Accept-Language")
    locale = _header(request, "X-Locale", "X-UI-Language") or accept_lang.split(",")[0].strip()
    return {
        "device_id": extract_client_device(request, x_device_id),
        "ip": extract_client_ip(request),
        "app_version": _header(request, "X-App-Version", "X-App-Version-Name"),
        "os": _header(request, "X-OS", "X-Platform"),
        "os_version": _header(request, "X-OS-Version"),
        "device_model": _header(request, "X-Device-Model"),
        "locale": locale[:16],
        "timezone": _header(request, "X-Timezone"),
        "storefront_country": _header(request, "X-Storefront-Country", "X-Country-Code"),
        "ip_country": _header(request, "X-IP-Country", "X-Country-Code"),
    }


def extract_client_context(request: Request, x_device_id: Optional[str] = None) -> Dict[str, Any]:
    return extract_growth_headers(request, x_device_id=x_device_id)
