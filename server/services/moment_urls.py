"""朋友圈图片 URL 校验 / 预览回退。"""
from __future__ import annotations

from typing import Optional


def image_url_valid(image_url: Optional[str]) -> Optional[str]:
    """过滤空值、生成中标记、明显错误地址。"""
    if not image_url:
        return None
    cleaned = image_url.strip()
    if (
        not cleaned
        or cleaned == "__GENERATING__"
        or "/src/" in cleaned
        or "main.tsx" in cleaned
        or cleaned.endswith((".tsx", ".ts", ".js", ".map"))
        or "?t=" in cleaned
        or "placeholder" in cleaned.lower()
    ):
        return None
    return cleaned


def optional_image_url(raw) -> Optional[str]:
    """JSON null / 空串 → None。"""
    if raw is None:
        return None
    return str(raw).strip() or None


def normalize_preview_pos(raw) -> Optional[dict]:
    """
    预览窗位置。x/y 均为 0~1：盖住 4:3 窗口后还能滑的范围。
    0 = 顶/左，1 = 底/右。null 表示清空。不要用像素。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid_preview_pos")
    if "x" not in raw or "y" not in raw:
        raise ValueError("invalid_preview_pos")
    try:
        x = float(raw["x"])
        y = float(raw["y"])
    except (TypeError, ValueError):
        raise ValueError("invalid_preview_pos")
    if x < 0 or x > 1 or y < 0 or y > 1:
        raise ValueError("invalid_preview_pos")
    if x != x or y != y:  # NaN
        raise ValueError("invalid_preview_pos")
    return {"x": round(x, 6), "y": round(y, 6)}


def moment_preview_pos(moment) -> Optional[dict]:
    raw = getattr(moment, "preview_pos", None)
    if not isinstance(raw, dict):
        return None
    try:
        return normalize_preview_pos(raw)
    except ValueError:
        return None


def moment_public_image_fields(moment) -> dict:
    """用户端：image_url 原图；preview_url 列表预览，未设置时回退原图。"""
    original = image_url_valid(getattr(moment, "image_url", None))
    preview = image_url_valid(getattr(moment, "preview_url", None))
    return {
        "image_url": original,
        "preview_url": preview or original,
        "preview_pos": moment_preview_pos(moment),
        "image_generating": getattr(moment, "image_url", None) == "__GENERATING__",
    }
