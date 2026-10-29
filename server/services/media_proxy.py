"""管理端同源图片代理：绕过 GCS/COS 桶 CORS，供裁切/canvas 读像素。

展示用 <img> 仍走原始公开 URL，不需要本代理。
仅允许本项目存储域名，防 SSRF。
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Optional, Set, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_MAX_BYTES = 25 * 1024 * 1024  # 25MB
_TIMEOUT = 20


def _env_hosts() -> Set[str]:
    hosts: Set[str] = set()
    bucket = (os.environ.get("GCS_BUCKET") or "").strip()
    if bucket:
        hosts.add("storage.googleapis.com")
    custom = (os.environ.get("GCS_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if custom:
        try:
            p = urlparse(custom if "://" in custom else f"https://{custom}")
            if p.hostname:
                hosts.add(p.hostname.lower())
        except Exception:
            pass
    cos_domain = (os.environ.get("COS_DOMAIN") or "").strip().lower()
    if cos_domain:
        hosts.add(cos_domain.split(":")[0])
    # 常见腾讯云 COS 后缀（桶名可能变，按后缀匹配）
    return hosts


def _gcs_path_allowed(path: str) -> bool:
    bucket = (os.environ.get("GCS_BUCKET") or "").strip()
    if not bucket:
        return False
    # /trandsbucket/moments/xxx.png
    return path.startswith(f"/{bucket}/")


def _is_blocked_ip(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


def is_allowed_media_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw or len(raw) > 2048:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost",) or host.endswith(".local"):
        return False

    allowed = _env_hosts()
    path = parsed.path or "/"

    if host == "storage.googleapis.com":
        return _gcs_path_allowed(path)

    if host in allowed:
        return True

    # myqcloud.com COS：*.cos.*.myqcloud.com
    if host.endswith(".myqcloud.com") and ".cos." in host:
        return True

    return False


def fetch_media(url: str) -> Tuple[bytes, str]:
    """拉取允许的公开媒体。成功返回 (bytes, content_type)。"""
    raw = (url or "").strip()
    if not is_allowed_media_url(raw):
        raise ValueError("url_not_allowed")

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if _is_blocked_ip(host):
        raise ValueError("url_not_allowed")

    try:
        resp = requests.get(
            raw,
            timeout=_TIMEOUT,
            stream=True,
            headers={"User-Agent": "trandsai-admin-media-proxy/1.0"},
        )
    except requests.RequestException as e:
        logger.warning("media_proxy fetch failed url=%s err=%s", raw[:200], e)
        raise ValueError("fetch_failed") from e

    if resp.status_code != 200:
        logger.warning("media_proxy bad status=%s url=%s", resp.status_code, raw[:200])
        raise ValueError("fetch_failed")

    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type and not (
        content_type.startswith("image/")
        or content_type in ("application/octet-stream", "binary/octet-stream")
    ):
        raise ValueError("not_image")

    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > _MAX_BYTES:
            raise ValueError("too_large")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValueError("fetch_failed")

    if not content_type or content_type == "application/octet-stream":
        lower = (parsed.path or "").lower()
        if lower.endswith(".png"):
            content_type = "image/png"
        elif lower.endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"
        elif lower.endswith(".webp"):
            content_type = "image/webp"
        elif lower.endswith(".gif"):
            content_type = "image/gif"
        else:
            content_type = "image/png"

    return data, content_type


def proxy_path_for(url: str) -> Optional[str]:
    """生成管理端代理路径（不含 host），url 非法返回 None。"""
    raw = (url or "").strip()
    if not is_allowed_media_url(raw):
        return None
    from urllib.parse import quote

    return f"/api/admin/media/proxy?url={quote(raw, safe='')}"
