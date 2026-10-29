"""图片缩略图生成（N-07）：上传时写 thumb，列表页优先加载小图。"""
from __future__ import annotations

import io
import logging
import os
import re
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


# 智能体头像：适度压缩，兼顾清晰度与流量（可用环境变量覆盖）
AVATAR_MAX_EDGE = int(os.getenv("AVATAR_MAX_EDGE", "512"))
AVATAR_QUALITY = int(os.getenv("AVATAR_QUALITY", "82"))


def compress_avatar_bytes(
    content: bytes,
    max_edge: int = AVATAR_MAX_EDGE,
    quality: int = AVATAR_QUALITY,
) -> Optional[bytes]:
    """压缩智能体头像：最长边限制 + WebP，失败返回 None（调用方用原图）。"""
    if not content:
        return None
    out = make_thumbnail_bytes(content, max_edge=max_edge, quality=quality)
    if not out:
        return None
    # 压缩后反而更大则丢弃（极少见，如已是高压缩 webp）
    if len(out) >= len(content):
        return None
    return out


# 发帖/通用上传：接收大原图后压到可存尺寸（可用环境变量覆盖）
UPLOAD_MAX_EDGE = int(os.getenv("UPLOAD_MAX_EDGE", "2560"))
UPLOAD_JPEG_QUALITY = int(os.getenv("UPLOAD_JPEG_QUALITY", "85"))
UPLOAD_STORE_MAX_BYTES = int(os.getenv("UPLOAD_STORE_MAX_BYTES", str(10 * 1024 * 1024)))
_UPLOAD_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]{0,62}$")


def normalize_upload_folder(raw: Optional[str]) -> str:
    """COS 目录：空则 images；只允许字母数字、_、-、/，禁止 .."""
    s = (raw or "").strip().replace("\\", "/").strip("/")
    if not s:
        return "images"
    if ".." in s or not _UPLOAD_FOLDER_RE.match(s):
        raise ValueError("invalid_folder")
    return s


def parse_upload_quality(raw) -> Optional[int]:
    """质量 1–100；空表示沿用默认压缩策略。"""
    if raw is None or raw == "":
        return None
    try:
        q = int(raw)
    except (TypeError, ValueError):
        raise ValueError("invalid_quality")
    if q < 1 or q > 100:
        raise ValueError("invalid_quality")
    return q


def parse_upload_max_edge(raw) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        edge = int(raw)
    except (TypeError, ValueError):
        raise ValueError("invalid_max_edge")
    if edge < 320 or edge > 4096:
        raise ValueError("invalid_max_edge")
    return edge


def _ensure_heif_opener() -> None:
    """可选注册 HEIC/HEIF，未安装 pillow-heif 则跳过。"""
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        pass


def sniff_image_mime(content: bytes) -> Optional[str]:
    """根据文件头推断 MIME（移动端 content_type 常不准）。"""
    if not content or len(content) < 12:
        return None
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    # HEIC/HEIF: ....ftypheic / heif / mif1 / msf1
    if content[4:8] == b"ftyp":
        brand = content[8:12]
        if brand in (
            b"heic", b"heix", b"heif", b"hevc", b"hevx",
            b"mif1", b"msf1", b"heim", b"heis",
        ):
            return "image/heic"
    return None


def prepare_upload_image(
    content: bytes,
    content_type: str = "",
    *,
    max_edge: int = UPLOAD_MAX_EDGE,
    quality: int = UPLOAD_JPEG_QUALITY,
    store_max_bytes: int = UPLOAD_STORE_MAX_BYTES,
    force_compress: bool = False,
) -> Tuple[Optional[Tuple[bytes, str, str]], str]:
    """
    规范化并发帖上传图：解码（含可选 HEIC）→ 缩放 → JPEG 压到 store_max 内。

    返回 ((bytes, mime, ext), "")；失败返回 (None, reason_key)。
    未指定 force_compress 时：小体积且已是允许格式尽量原样返回。
    """
    if not content:
        return None, "image_empty"

    mime = (content_type or "").split(";")[0].strip().lower()
    sniffed = sniff_image_mime(content)
    if sniffed:
        mime = sniffed

    allowed_passthrough = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    # 已够小且格式合法：直接存，保留 GIF 动画等（显式 quality 时强制再压）
    if (
        not force_compress
        and mime in allowed_passthrough
        and len(content) <= store_max_bytes
    ):
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }[mime]
        return (content, mime, ext), ""

    _ensure_heif_opener()
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未安装，无法压缩上传图")
        return None, "image_compress_failed"

    try:
        img = Image.open(io.BytesIO(content))
        # 动图：压不动就原样（若已超限则取首帧转 JPEG）
        n_frames = int(getattr(img, "n_frames", 1) or 1)
        if (
            not force_compress
            and n_frames > 1
            and len(content) <= store_max_bytes
            and mime == "image/gif"
        ):
            return (content, "image/gif", ".gif"), ""

        if n_frames > 1:
            img.seek(0)

        if img.mode in ("RGBA", "P"):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        edge = max(max_edge, 320)
        q = max(10, min(int(quality), 95))
        data: Optional[bytes] = None

        for _ in range(8):
            work = img.copy()
            work.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            work.save(buf, format="JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            if len(data) <= store_max_bytes:
                break
            if q > 55:
                q -= 10
            else:
                edge = int(edge * 0.75)
                if edge < 320:
                    break

        if not data:
            return None, "image_compress_failed"
        if len(data) > store_max_bytes:
            logger.warning(
                "上传图压缩后仍超限: %d bytes (limit=%d)",
                len(data),
                store_max_bytes,
            )
            return None, "image_compress_still_too_large"

        logger.info(
            "上传图已压缩 %d -> %d bytes (edge≈%d q=%d mime=%s)",
            len(content),
            len(data),
            edge,
            q,
            mime or "?",
        )
        return (data, "image/jpeg", ".jpg"), ""
    except Exception as e:
        logger.warning("上传图处理失败 mime=%s: %s", mime or "?", e)
        if mime in ("image/heic", "image/heif") or "heif" in str(e).lower() or "heic" in str(e).lower():
            return None, "image_heic_decode_failed"
        return None, "image_decode_failed"


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
