import hashlib
import base64
import json
import logging
import os
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple
from dotenv import load_dotenv
import requests  # 统一在顶部导入，避免函数内重复 import
load_dotenv(override=True)
from core.config import BASE_DIR

from services.cos_storage import is_cos_enabled, upload_file_to_cos, upload_bytes_to_cos
logger = logging.getLogger(__name__)

# Gemini 文生图（默认主路径）
_GOOGLE_API_KEY = (
    os.environ.get("GOOGLE_API_KEY", "")
    or os.environ.get("GOOGLEAI_API_KEY", "")
)
_GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
_GEMINI_API_BASE = os.getenv(
    "GEMINI_API_BASE",
    "https://generativelanguage.googleapis.com/v1beta",
)

# xAI API 配置（降级备用）
_XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
_XAI_IMAGE_API_URL = "https://api.x.ai/v1/images/generations"

# 阿里云百炼 DashScope 文生图配置（可选备用，默认未启用）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
API_URL = os.getenv("API_URL")
MODEL_NAME = os.getenv("MODEL_NAME")


# 图片风格增强词
_STYLE_ENHANCEMENTS = {
    "anime": "masterpiece, best quality, detailed illustration, vibrant colors, soft lighting",
    "realistic": "photorealistic, 8k ultra high quality, detailed, natural lighting, professional photography",
    "portrait": "detailed face, soft lighting, high quality, looking at camera, upper body portrait",
}

_IMAGE_GEN_CONFIG_CACHE: List[dict] = []
_IMAGE_GEN_CONFIG_CACHE_TS = 0.0
_IMAGE_GEN_CONFIG_CACHE_TTL_SECONDS = 300.0  # 优化：从30s增加到5分钟，减少高频批量生成时的DB查询

# Gemini 管理端：比例 + 清晰度档（非精确像素）
GEMINI_ASPECT_RATIOS: Tuple[str, ...] = (
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "4:5",
    "5:4",
    "21:9",
)
GEMINI_IMAGE_SIZES: Tuple[str, ...] = ("1K", "2K", "4K")


def _get_active_image_gen_configs() -> List[dict]:
    """从数据库获取所有启用的图片生成配置（按 sort_order 排序）"""
    global _IMAGE_GEN_CONFIG_CACHE, _IMAGE_GEN_CONFIG_CACHE_TS
    now = time.time()
    if (
        _IMAGE_GEN_CONFIG_CACHE
        and now - _IMAGE_GEN_CONFIG_CACHE_TS < _IMAGE_GEN_CONFIG_CACHE_TTL_SECONDS
    ):
        return _IMAGE_GEN_CONFIG_CACHE

    try:
        from core.database import ConfigGroupORM, get_db
        with get_db() as db:
            rows = db.query(ConfigGroupORM).filter(
                ConfigGroupORM.config_type == "image_generation",
                ConfigGroupORM.enabled == 1,
            ).order_by(ConfigGroupORM.sort_order.asc()).all()
            configs = [r.config_json or {} for r in rows]
            _IMAGE_GEN_CONFIG_CACHE = configs
            _IMAGE_GEN_CONFIG_CACHE_TS = now
            return configs
    except Exception:
        if _IMAGE_GEN_CONFIG_CACHE:
            logger.warning("Load image-generation configs failed, using cached configs")
            return _IMAGE_GEN_CONFIG_CACHE
        logger.warning("Load image-generation configs failed and no cache available")
    return []


def _gemini_aspect_ratio(width: int, height: int) -> str:
    """由像素宽高推导 Gemini 常见 aspect_ratio。"""
    w = max(int(width or 0), 1)
    h = max(int(height or 0), 1)
    target = w / h
    candidates = (
        (1, 1, "1:1"),
        (16, 9, "16:9"),
        (9, 16, "9:16"),
        (4, 3, "4:3"),
        (3, 4, "3:4"),
        (3, 2, "3:2"),
        (2, 3, "2:3"),
    )
    best = "1:1"
    best_err = float("inf")
    for rw, rh, label in candidates:
        err = abs(target - (rw / rh))
        if err < best_err:
            best_err = err
            best = label
    return best


def generate_image_with_gemini(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    config: Optional[dict] = None,
    *,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
) -> Optional[bytes]:
    """使用 Gemini 图像模型生成图片，返回原始图片 bytes（再上传 COS）。

    管理端传 aspect_ratio + image_size；其它内部调用可只传 width/height（推比例，默认 1K）。
    """
    cfg = config or {}
    api_key = (cfg.get("api_key") or _GOOGLE_API_KEY or "").strip()
    if not api_key:
        logger.info("Gemini image generation skipped: missing GOOGLE_API_KEY")
        return None

    model = (cfg.get("model") or _GEMINI_IMAGE_MODEL or "gemini-2.5-flash-image").strip()
    if model in ("gemini-flash-latest", "gemini-2.0-flash", "gemini-pro"):
        logger.warning("model %s 不支持出图，改用 gemini-2.5-flash-image", model)
        model = "gemini-2.5-flash-image"

    base = (cfg.get("base_url") or _GEMINI_API_BASE).rstrip("/")
    if base.endswith(":generateContent"):
        url = base
    elif "/models/" in base:
        url = f"{base}:generateContent"
    else:
        url = f"{base}/models/{model}:generateContent"

    prompt_clean = (prompt or "").strip()
    if len(prompt_clean) > 2500:
        prompt_clean = prompt_clean[:2500] + "..."

    ar = (aspect_ratio or "").strip() or _gemini_aspect_ratio(width, height)
    size = (image_size or "").strip().upper() or "1K"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt_clean}],
            }
        ],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": ar,
                "imageSize": size,
            },
        },
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": api_key,
            },
            json=payload,
            timeout=120,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Gemini image HTTP %s: %s",
                resp.status_code,
                (resp.text or "")[:800],
            )
            return None

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            logger.warning(
                "Gemini image empty candidates: %s",
                json.dumps(data, ensure_ascii=False)[:500],
            )
            return None

        parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            b64 = inline.get("data")
            if b64:
                raw = base64.b64decode(b64)
                if len(raw) > 500:
                    logger.info(
                        "Gemini image generation succeeded model=%s aspect=%s size=%s bytes=%d",
                        model,
                        ar,
                        size,
                        len(raw),
                    )
                    return raw
        logger.warning("Gemini image response has no inline image data")
    except requests.exceptions.RequestException as e:
        detail = ""
        r = getattr(e, "response", None)
        if r is not None:
            try:
                detail = (r.text or "")[:800]
            except Exception:
                pass
        logger.warning("Gemini image HTTP failed: %s %s", e, detail or "")
    except Exception as e:
        logger.warning("Gemini image generation failed: %s", e)
    return None


def generate_image_with_alibaba(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    config: Optional[dict] = None,
) -> Optional[str]:
    # 强制忽略数据库config，只用全局密钥
    config = None
    logger.info("===== 进入阿里云绘图函数，开始执行 =====")
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
        },
        "parameters": {
            "size": f"{width}*{height}",
            "n": 1
        }
    }
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        res_data = resp.json()

        logger.info("阿里云完整返回报文: %s", json.dumps(res_data, ensure_ascii=False))

        output = res_data.get("output", {})
        # 修复1：读取choices，不是results
        choices = output.get("choices", [])
        if not choices:
            logger.warning("Alibaba API empty choices")
            return None

        content_list = choices[0].get("message", {}).get("content", [])
        img_url = None
        for item in content_list:
            # 修复2：接口无type字段，直接读取image键
            if "image" in item:
                img_url = item.get("image")
                break
        if not img_url:
            logger.warning("Alibaba no image url returned")
            return None
        return img_url

    except requests.exceptions.RequestException as e:
        detail = ""
        resp = getattr(e, "response", None)
        if resp:
            detail = resp.text[:1200]
        logger.warning("Alibaba HTTP failed %s | %s", e, detail)
    except Exception as e:
        logger.warning("Alibaba generate error %s", e)
    return None




def _get_cos_cache_key(prompt: str) -> str:
    """根据 prompt 生成 COS 缓存 key"""
    key = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    return f"images/{key}.png"


def _upload_image_to_cos(data: bytes, filename: str = None) -> str:
    """上传图片数据到 COS，返回 COS URL。
    如果 COS 未配置或上传失败，返回空字符串。
    """
    if not filename:
        filename = f"{hashlib.md5(data).hexdigest()[:16]}.png"

    cos_key = f"images/{filename}"
    url = upload_bytes_to_cos(data, cos_key)
    if url:
        return url
    logger.warning("COS 上传失败: %s", filename)
    return ""

def _build_pollinations_url(
    prompt: str,
    width: int = 600,
    height: int = 600,
    model: str = "",
    seed: Optional[int] = None,
    nologo: bool = True,
) -> str:
    """构建 Pollinations.ai 图片生成 URL"""
    encoded = urllib.parse.quote(prompt)
    params = f"width={width}&height={height}"
    if model:
        params += f"&model={model}"
    if seed is not None:
        params += f"&seed={seed}"
    else:
        params += f"&seed={random.randint(1, 1000000)}"
    if nologo:
        params += "&nologo=true"
    return f"https://image.pollinations.ai/prompt/{encoded}?{params}"


class ImageGenerationError(RuntimeError):
    """所有图片生成上游均失败，或结果不可用。"""


# xAI Imagine 支持的常见宽高比（按与目标比例的接近程度匹配）
_XAI_ASPECT_RATIOS = (
    (1, 1, "1:1"),
    (16, 9, "16:9"),
    (9, 16, "9:16"),
    (4, 3, "4:3"),
    (3, 4, "3:4"),
    (3, 2, "3:2"),
    (2, 3, "2:3"),
    (2, 1, "2:1"),
    (1, 2, "1:2"),
    (19.5, 9, "19.5:9"),
    (9, 19.5, "9:19.5"),
    (20, 9, "20:9"),
    (9, 20, "9:20"),
)


def aspect_ratio_from_size(width: int, height: int) -> str:
    """由像素宽高推导最接近的 xAI aspect_ratio 字符串。"""
    w = max(int(width or 0), 1)
    h = max(int(height or 0), 1)
    target = w / h
    best = "1:1"
    best_err = float("inf")
    for rw, rh, label in _XAI_ASPECT_RATIOS:
        err = abs(target - (rw / rh))
        if err < best_err:
            best_err = err
            best = label
    return best


def generate_image_with_xai(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    config: dict = None,
    *,
    aspect_ratio: Optional[str] = None,
) -> Optional[str]:
    """使用 xAI (Grok) API 生成图片，返回图片 URL。只认 aspect_ratio。"""
    api_key = (config or {}).get("api_key") or _XAI_API_KEY
    if not api_key:
        logger.info("xAI image generation skipped: missing XAI_API_KEY")
        return None

    model = (config or {}).get("model", "grok-imagine-image-quality")
    base_url = (config or {}).get("base_url", _XAI_IMAGE_API_URL)
    ar = (aspect_ratio or "").strip() or aspect_ratio_from_size(width, height)

    try:
        body = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "response_format": "url",
            "aspect_ratio": ar,
        }
        payload = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            base_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("data") and len(data["data"]) > 0:
                url = data["data"][0].get("url")
                if url:
                    logger.info("xAI image generation succeeded aspect_ratio=%s", ar)
                    return url
    except Exception as e:
        logger.warning("xAI image generation failed: %s", e)

    return None


def generate_image_with_xai_cached(
    prompt: str,
    style: str = "anime",
    width: int = 1024,
    height: int = 1024,
) -> str:
    """使用 xAI 生成图片并上传到COS。返回COS URL。
    如果 xAI 不可用，回退到通用生成管道。
    """
    enhancement = _STYLE_ENHANCEMENTS.get(style, _STYLE_ENHANCEMENTS["anime"])
    full_prompt = f"{prompt}. {enhancement}"

    # 尝试 xAI
    xai_url = generate_image_with_xai(full_prompt, width, height)
    if xai_url:
        cos_url = _download_and_upload_to_cos(xai_url)
        if cos_url and cos_url.startswith("http"):
            return cos_url

    # 回退到通用生成管道
    logger.info("xAI generation unavailable, fallback to generic pipeline")
    return generate_image_with_cache(prompt, style, width, height)


def _download_and_upload_to_cos(
    url: str,
    filename: str = None,
    *,
    compress_avatar: bool = False,
) -> str:
    """下载外部图片并上传到COS，返回COS URL。
    如果下载或上传失败，返回原始URL。
    compress_avatar=True 时先缩放压缩再上传（智能体头像）。
    """
    if not url or not url.startswith(('http://', 'https://')):
        return url

    # 生成文件名
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:16]
    if not filename:
        filename = f"{url_hash}.png"

    # 下载图片
    try:
        import requests
        resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        image_data = resp.content

        # 检查文件大小（避免下载空文件或过大的文件）
        if len(image_data) < 1000:
            logger.warning("Downloaded image too small (%d bytes), skipping upload", len(image_data))
            return url

        if compress_avatar:
            try:
                from services.thumbnails import compress_avatar_bytes

                compressed = compress_avatar_bytes(image_data)
                if compressed:
                    logger.info(
                        "Avatar compressed %d -> %d bytes (%.0f%%)",
                        len(image_data),
                        len(compressed),
                        100.0 * len(compressed) / max(len(image_data), 1),
                    )
                    image_data = compressed
                    filename = f"{Path(filename).stem}.webp"
            except Exception as ce:
                logger.warning("Avatar compress skipped: %s", ce)

        # 上传到COS
        cos_key = f"images/{filename}"
        cos_url = upload_bytes_to_cos(image_data, cos_key)
        if cos_url:
            logger.info("Downloaded and uploaded to COS: %s -> %s", url[:50], cos_url[:80])
            return cos_url

        # COS上传失败，返回原始URL
        logger.warning("COS upload failed for %s, returning original URL", filename)
        return url

    except Exception as e:
        logger.warning("Failed to download and upload image from %s: %s", url[:50], e)
        return url


def _generate_fallback_image(width: int = 600, height: int = 600) -> str:
    """当外部 API 全部失败时抛错，不再静默返回空串。"""
    logger.warning("所有图片生成服务均失败，无可用的回退方案")
    raise ImageGenerationError("image generation failed: no upstream provider available")


def generate_image(
    prompt: str,
    style: str = "",
    width: int = 600,
    height: int = 600,
    model: str = "",
    seed: Optional[int] = None,
    nologo: bool = True,
    compress_avatar: bool = False,
    *,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
) -> str:
    """生成图片并返回 COS URL。管理端请传 aspect_ratio + image_size。"""
    return generate_image_with_cache(
        prompt=prompt,
        style=style or "anime",
        width=width,
        height=height,
        model=model,
        seed=seed,
        nologo=nologo,
        compress_avatar=compress_avatar,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )


def generate_image_with_cache(
    prompt: str,
    style: str = "anime",
    width: int = 600,
    height: int = 600,
    model: str = "",
    seed: Optional[int] = None,
    nologo: bool = True,
    compress_avatar: bool = False,
    *,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
) -> str:
    """生成图片并上传到 COS。未传 aspect_ratio 时由 width/height 推导（头像/朋友圈等内部调用）。"""
    enhancement = _STYLE_ENHANCEMENTS.get(style, _STYLE_ENHANCEMENTS["anime"])
    full_prompt = f"{prompt}. {enhancement}"

    ar = (aspect_ratio or "").strip() or _gemini_aspect_ratio(width, height)
    size = (image_size or "").strip().upper() or "1K"

    configs = _get_active_image_gen_configs()

    gemini_config = None
    for cfg in configs:
        p = (cfg.get("provider") or "").lower()
        if p in ("google", "gemini"):
            gemini_config = cfg
            break
    raw = generate_image_with_gemini(
        full_prompt,
        width,
        height,
        config=gemini_config,
        aspect_ratio=ar,
        image_size=size,
    )
    if raw:
        filename = f"{hashlib.md5(raw).hexdigest()[:16]}.png"
        if compress_avatar:
            try:
                from services.thumbnails import compress_avatar_bytes
                compressed = compress_avatar_bytes(raw)
                if compressed:
                    raw = compressed
                    filename = f"{Path(filename).stem}.webp"
            except Exception as ce:
                logger.warning("Avatar compress skipped: %s", ce)
        cos_url = _upload_image_to_cos(raw, filename=filename)
        if cos_url and cos_url.startswith("http"):
            return cos_url

    xai_config = None
    xai_configs = [
        cfg for cfg in configs
        if (cfg.get("provider") or "").lower() in ("grok", "xai")
    ]
    if xai_configs:
        xai_config = xai_configs[0]
    if xai_config or _XAI_API_KEY:
        url = generate_image_with_xai(
            full_prompt,
            width,
            height,
            config=xai_config,
            aspect_ratio=ar,
        )
        if url:
            cos_url = _download_and_upload_to_cos(url, compress_avatar=compress_avatar)
            if cos_url and cos_url.startswith("http"):
                return cos_url

    logger.warning("所有图片生成服务均失败")
    raise ImageGenerationError("image generation failed: all providers failed")


def generate_image_bytes(
    prompt: str,
    style: str = "anime",
    width: int = 600,
    height: int = 600,
    model: str = "",
    seed: Optional[int] = None,
    nologo: bool = True,
    *,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
) -> Tuple[bytes, str]:
    """生成图片并返回原始 bytes（不上传 COS），供管理端预览/重生成。"""
    enhancement = _STYLE_ENHANCEMENTS.get(style, _STYLE_ENHANCEMENTS["anime"])
    full_prompt = f"{prompt}. {enhancement}"

    ar = (aspect_ratio or "").strip() or _gemini_aspect_ratio(width, height)
    size = (image_size or "").strip().upper() or "1K"

    configs = _get_active_image_gen_configs()

    gemini_config = None
    for cfg in configs:
        p = (cfg.get("provider") or "").lower()
        if p in ("google", "gemini"):
            gemini_config = cfg
            break
    raw = generate_image_with_gemini(
        full_prompt,
        width,
        height,
        config=gemini_config,
        aspect_ratio=ar,
        image_size=size,
    )
    if raw:
        return raw, "image/png"

    xai_config = None
    xai_configs = [
        cfg for cfg in configs
        if (cfg.get("provider") or "").lower() in ("grok", "xai")
    ]
    if xai_configs:
        xai_config = xai_configs[0]
    if xai_config or _XAI_API_KEY:
        url = generate_image_with_xai(
            full_prompt,
            width,
            height,
            config=xai_config,
            aspect_ratio=ar,
        )
        if url:
            resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            media_type = (resp.headers.get("content-type") or "image/png").split(";")[0].strip()
            if not media_type.startswith("image/"):
                media_type = "image/png"
            return resp.content, media_type

    logger.warning("所有图片生成服务均失败（bytes 模式）")
    raise ImageGenerationError("image generation failed: all providers failed")


def generate_avatar_prompt(profile: dict, style: str = "anime") -> str:
    """基于人设生成头像图片 prompt。

    Args:
        profile: 智能体人设字典，可包含 gender, age, city, personality,
                 background, hobbies, mbti, values, favorite_things 等字段。
        style: 头像风格，"anime" 为动漫风格（默认），"realistic" 为写实风格。

    Returns:
        完整的英文/中文混合 prompt 字符串。
    """
    gender = profile.get("gender", "女")
    gender_en = _gender_en_young(gender)
    age = _safe_age(profile.get("age", 22))
    city = profile.get("city", "")
    personality = (profile.get("personality", "") or "")[:80]
    background = (profile.get("background", "") or "")[:80]
    hobbies = (profile.get("hobbies", "") or "")[:60]
    mbti = profile.get("mbti", "")
    values = (profile.get("values", "") or "")[:60]
    favorite_things = (profile.get("favorite_things", "") or "")[:60]
    speech_style = (profile.get("speech_style", "") or "")[:60]
    appearance = (profile.get("appearance", "") or "")[:120]

    # ---------- 1. 收集人设特征描述 ----------
    trait_parts = []
    if appearance:
        trait_parts.append(f"外貌：{appearance}")
    if personality:
        trait_parts.append(personality)
    if background:
        trait_parts.append(background)
    if hobbies:
        trait_parts.append(f"喜爱{hobbies}")
    if values:
        trait_parts.append(values)
    if favorite_things:
        trait_parts.append(f"钟爱{favorite_things}")
    if speech_style:
        trait_parts.append(f"说话风格：{speech_style}")

    character_info = "，".join(trait_parts) if trait_parts else "性格友好且有魅力"

    # ---------- 2. MBTI 气质映射 ----------
    mbti_vibe = ""
    if mbti:
        vibe_map = {
            "intj": "mysterious confident intellectual gaze",
            "intp": "curious thoughtful dreamy expression",
            "entj": "charismatic assertive commanding presence",
            "entp": "playful witty energetic smile",
            "infj": "gentle empathetic serene expression",
            "infp": "whimsical idealistic soft gaze",
            "enfj": "warm inspiring radiant smile",
            "enfp": "cheerful creative sparkling eyes",
            "istj": "reliable composed calm demeanor",
            "isfj": "nurturing gentle caring eyes",
            "estj": "practical organized determined look",
            "esfj": "sociable supportive friendly smile",
            "istp": "cool independent sharp eyes",
            "isfp": "artistic sensitive tender expression",
            "estp": "bold adventurous confident grin",
            "esfp": "lively spontaneous joyful expression",
        }
        mbti_vibe = vibe_map.get(mbti.lower(), "")

    # ---------- 3. 年龄外观描述（始终锁年轻，禁止「成熟/沉稳」把人画老） ----------
    age_desc = _youthful_age_desc(age)
    age_lock = youthful_look_clause(age)

    # ---------- 4. 根据风格生成 prompt ----------
    if style == "realistic":
        prompt = f"Professional portrait photograph of a {age}-year-old {gender_en}"
        if city:
            prompt += f" from {city}"
        prompt += (
            f". {character_info}. {age_desc}. {age_lock}"
        )
        if mbti_vibe:
            prompt += f". {mbti_vibe}"
        prompt += (
            ". Natural soft lighting, golden hour glow, shallow depth of field, "
            "photorealistic, 8k ultra high quality, detailed skin texture, "
            "professional DSLR photography, subtle bokeh background, "
            "upper body portrait, looking directly at camera, natural gentle expression, "
            "sharp focus on eyes, cinematic color grading, flattering angle. "
            f"{age_lock}"
        )
    else:
        # anime 风格（默认）
        prompt = f"Stunning anime portrait of a {age}-year-old {gender_en} character"
        if city:
            prompt += f" from {city}"
        prompt += (
            f". {character_info}. {age_desc}. {age_lock}"
        )
        if mbti_vibe:
            prompt += f". {mbti_vibe}"
        prompt += (
            ". Large expressive detailed eyes, delicate beautiful facial features, "
            "soft pastel color palette, clean smooth line art, "
            "masterpiece illustration quality, soft gradient or minimal background, "
            "beautiful lighting on face, best quality, highly detailed, "
            "professional anime artwork, studio quality. "
            f"{age_lock}"
        )

    return prompt


def _safe_age(raw, default: int = 22) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(16, min(n, 80))


def _gender_en_young(gender: str) -> str:
    if gender == "女":
        return "young woman"
    if gender == "男":
        return "young man"
    return "young adult"


def youthful_look_clause(age: int) -> str:
    """Lock generated faces to the listed age; block middle-aged/elderly drift."""
    age_i = _safe_age(age)
    if age_i <= 22:
        look = "teen-to-early-20s face, rounder cheeks, baby-smooth skin, youthful eyes"
    elif age_i <= 26:
        look = "early-to-mid 20s young-adult face, fresh, smooth skin, no aging"
    else:
        look = f"visually a real {age_i}-year-old young adult, still youthful, college-to-young-professional look"
    return (
        f"AGE LOCK: must look exactly {age_i} years old ({look}). "
        "Youthful only: no wrinkles, no crow's feet, no nasolabial folds, no sagging, "
        "no grey or white hair, no receding hairline, no extra weight of middle age, "
        "not 35-60, not aunt/uncle, not elderly, not mature-aged."
    )


def _youthful_age_desc(age: int) -> str:
    age_i = _safe_age(age)
    if age_i <= 20:
        return "youthful early-20s look, fresh innocent face, smooth skin"
    if age_i <= 24:
        return "young vibrant early-20s adult, energetic, no signs of aging"
    if age_i <= 28:
        return "mid-20s young adult, attractive youthful face, smooth skin, NOT middle-aged"
    return f"looks {age_i} but still clearly a young adult, youthful, NOT middle-aged or elderly"


def augment_prompt_with_style(prompt: str, style: str) -> str:
    """与 generate_image_with_cache 一致：在 prompt 后拼接风格增强词（供仅需单字符串的脚本/API）。"""
    enhancement = _STYLE_ENHANCEMENTS.get(style, _STYLE_ENHANCEMENTS["anime"])
    return f"{prompt}. {enhancement}"


def _moment_caption_route(caption: str) -> Optional[str]:
    """根据文案粗判更适合「风景/静物」还是「人物出镜」；无法判断时返回 None。"""
    raw = caption or ""
    low = raw.lower()

    scene_kw = (
        "晚霞", "天空", "日落", "雨天", "月亮", "花开", "云海", "海景", "咖啡", "一桌菜", "美食",
        "窗外", "街景", "夜景", "樱花", "雪", "湖边", "山里", "风景", "夕阳", "云朵", "落叶",
        "sunset", "sky", "coffee", "latte", "ocean", "rain", "landscape", "scenery", "blooming",
    )
    person_kw = (
        "我", "自拍", "妆", "镜子", "脸", "合影", "穿搭", "今天穿", "拍我", "化妆", "素颜",
        "出门照", "对镜", "自拍杆", "selfie", "my face", "outfit", "wearing today", "me in ",
        "me at ", "my look",
    )

    has_scene = any(k in raw for k in scene_kw) or any(
        k in low for k in ("sunset", "sky", "coffee", "latte", "ocean", "rain", "landscape", "scenery", "blooming")
    )
    has_person = any(k in raw for k in person_kw) or any(
        k in low for k in ("selfie", "my face", "outfit", "wearing today", "me in ", "me at ", "my look")
    )

    if has_person and not has_scene:
        return "person"
    if has_scene and not has_person:
        return "scenery"
    if has_person and has_scene:
        return "person"
    return None


def _profile_favors_anime(profile: dict) -> bool:
    blob = f"{profile.get('personality', '')}{profile.get('hobbies', '')}".lower()
    keys = ("动漫", "二次元", "番剧", "漫画", "宅", "anime", "manga", "otaku", "cos", "acg")
    return any(k in blob for k in keys)


def generate_moment_image_prompt(caption: str, profile: dict = None) -> Tuple[str, str]:
    """基于朋友圈文案与人设生成配图 prompt，并给出与缓存管线一致的风格键。

    规则概要：
    - 可出现风景、静物、环境、动漫/插画或写实生活照等任意与文案、性格相符的类型。
    - 若画面中有清晰人脸/主角人物：必须同一套「 recurring companion 」人设（年龄、性别、性格气质与资料一致），多次出图保持可识别的同一角色感。
    - 纯风景/静物：不出现突出人脸（远处剪影可），氛围与资料中的城市、爱好、性格相衬。

    Returns:
        (prompt, style) — style 为 ``realistic`` 或 ``anime``，供 ``generate_image_with_cache`` 使用。
    """
    cap = (caption or "").strip()
    if len(cap) > 80:
        cap = cap[:80] + "..."

    common_tail = "no text overlays, no watermark, no captions in image"

    route = _moment_caption_route(cap)
    if route is None:
        route = random.choices(["scenery", "person"], weights=[0.42, 0.58], k=1)[0]

    if not profile:
        style = random.choice(["realistic", "anime"])
        if route == "scenery":
            p = (
                f"Social feed illustration for a post about: {cap}. "
                "Scenery, still life, environment, or mood shot matching the caption tone; "
                "variety welcome (photo-like or stylized art). "
                f"{common_tail}"
            )
        else:
            p = (
                f"Social feed image for a post about: {cap}. "
                "Natural relatable moment; style may be photorealistic or illustrated. "
                f"{common_tail}"
            )
        return (p, style)

    gender = profile.get("gender", "女")
    gender_en = _gender_en_young(gender)
    age = _safe_age(profile.get("age", 22))
    personality = (profile.get("personality", "") or "").strip()[:80]
    hobbies = (profile.get("hobbies", "") or "").strip()[:60]
    city = (profile.get("city", "") or "").strip()
    country = (profile.get("country", "") or "").strip().upper()
    region_label = (profile.get("region_label", "") or "").strip()
    mbti = (profile.get("mbti", "") or "").strip()

    key_trait = "warm and authentic"
    if personality:
        parts = [x.strip() for x in personality.replace("，", ",").replace("、", ",").split(",") if x.strip()]
        if parts:
            key_trait = parts[0][:40]

    loc_hint = ""
    if region_label or country or city:
        loc_hint = f"Locale atmosphere: {region_label or country or city}, city {city}. "
    hobby_hint = f"Tastes influenced by hobbies: {hobbies}. " if hobbies else ""
    mbti_hint = f"Subtle vibe hint: {mbti}. " if mbti else ""
    age_lock = youthful_look_clause(age)

    persona_lock = (
        f"RECURRING SAME CHARACTER if any face is shown: single {age}-year-old {gender_en}, "
        f"{age_lock} "
        f"personality essence: {key_trait}; {personality[:120] if personality else key_trait}. "
        "Keep facial identity and vibe consistent with this companion across images. "
    )

    favors_anime = _profile_favors_anime(profile)

    if route == "scenery":
        style = random.choice(["realistic", "anime"])
        if style == "realistic":
            p = (
                f"WeChat-Moments-style image for post: {cap}. "
                f"{loc_hint}{hobby_hint}{mbti_hint}"
                "Focus on landscape, city view, sky, food flat lay, cafe interior, objects, or mood environment — "
                "no prominent human face or portrait (tiny distant silhouettes OK). "
                "Must match caption mood and the poster's personality described above. "
                "Smartphone candid photo aesthetic, natural light, believable snapshot. "
                f"{common_tail}"
            )
        else:
            p = (
                f"Anime or high-quality illustrated social-feed image for post: {cap}. "
                f"{loc_hint}{hobby_hint}{mbti_hint}"
                "Scenery, background art, still life, or environmental shot; no detailed human face as subject. "
                "Mood and palette aligned with caption and personality. "
                f"{common_tail}"
            )
        return (p, style)

    # person in fram1e
    if favors_anime:
        style = "anime" if random.random() < 0.55 else "realistic"
    else:
        style = "anime" if random.random() < 0.32 else "realistic"

    location = f" in {city}" if city else ""

    if style == "realistic":
        p = (
            f"WeChat-Moments-style candid photo for post: {cap}. "
            f"{persona_lock}"
            f"{loc_hint}{hobby_hint}{mbti_hint}"
            f"Subject: the same {age}-year-old {gender_en}{location}, {key_trait} energy, "
            "pose and expression matching caption mood; youthful natural skin, smartphone or mirror-selfie realism, "
            "shallow depth of field, believable daily life. "
            f"{age_lock} {common_tail}"
        )
    else:
        p = (
            f"Anime / illustration social-feed image for post: {cap}. "
            f"{persona_lock}"
            f"{loc_hint}{hobby_hint}{mbti_hint}"
            f"Same recurring {age}-year-old {gender_en} character{location}, expressive eyes, clean line art, "
            f"scene and outfit matching caption; personality reads as {key_trait}. "
            f"{age_lock} {common_tail}"
        )
    return (p, style)
