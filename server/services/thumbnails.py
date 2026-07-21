"""图片缩略图生成（N-07）：上传时写 thumb，列表页优先加载小图。"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

THUMB_MAX_EDGE = int(os.getenv("THUMB_MAX_EDGE", "400"))
THUMB_QUALITY = int(os.getenv("THUMB_QUALITY", "78"))


def thumb_stem_name(original_filename: str) -> str:
    """abc.jpg -> abc_thumb.webp"""
    stem = Path(original_filename).stem
    return f"{stem}_thumb.webp"


def derive_thumb_url(original_url: str) -> Optional[str]:
    """根据原图 URL 推导约定缩略图路径（本地或 COS）。"""
    if not original_url or original_url.startswith("data:"):
        return None
    # 已是 thumb
    if "_thumb." in original_url:
        return original_url
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(original_url)
        path = parsed.path or ""
        if not path:
            return None
        p = Path(path)
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return None
        new_name = thumb_stem_name(p.name)
        new_path = str(p.with_name(new_name)).replace("\\", "/")
        return urlunparse(parsed._replace(path=new_path))
    except Exception:
        return None


def make_thumbnail_bytes(
    content: bytes,
    max_edge: int = THUMB_MAX_EDGE,
    quality: int = THUMB_QUALITY,
) -> Optional[bytes]:
    """将图片字节缩放到 max_edge，输出 WebP。GIF 取首帧。"""
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未安装，跳过缩略图生成")
        return None

    try:
        img = Image.open(io.BytesIO(content))
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")

        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        save_kwargs = {"format": "WEBP", "quality": quality, "method": 4}
        if img.mode == "RGBA":
            save_kwargs["format"] = "WEBP"
        img.save(buf, **save_kwargs)
        return buf.getvalue()
    except Exception as e:
        logger.warning("缩略图生成失败: %s", e)
        return None


def save_thumbnail_pair(
    content: bytes,
    image_dir: Path,
    original_filename: str,
) -> Tuple[Optional[Path], Optional[bytes]]:
    """
    在 image_dir 写入缩略图文件。
    返回 (thumb_path, thumb_bytes)。
    """
    thumb_bytes = make_thumbnail_bytes(content)
    if not thumb_bytes:
        return None, None
    thumb_name = thumb_stem_name(original_filename)
    thumb_path = image_dir / thumb_name
    try:
        thumb_path.write_bytes(thumb_bytes)
        return thumb_path, thumb_bytes
    except Exception as e:
        logger.warning("缩略图落盘失败: %s", e)
        return None, None
