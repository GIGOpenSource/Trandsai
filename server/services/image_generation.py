import hashlib
import json
import logging
import os
import random
import time
import urllib.parse
from pathlib import Path
from typing import List, Optional, Tuple

import requests  # 统一在顶部导入，避免函数内重123复 impor123t

from core.config import BASE_DIR

from services.cos_storage import is_cos_enabled, upload_file_to_cos, upload_bytes_to_cos
logger = logging.getLogger(__name__)

IMAGE_CACHE_DIR = Path(
    os.environ.get("IMAGE_STORAGE_DIR", str(Path(BASE_DIR) / "data" / "images"))
).expanduser().resolve()
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# xAI API 配置
_XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
_XAI_IMAGE_API_URL = "https://api.x.ai/v1/images/generations"

# 阿里云百炼 DashScope 文生图配置
_ALIBABA_DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
_ALIBABA_IMAGE_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"

# 图片风格增强词
_STYLE_ENHANCEMENTS = {
    "anime": "masterpiece, best quality, detailed illustration, vibrant colors, soft lighting",
    "realistic": "photorealistic, 8k ultra high quality, detailed, natural lighting, professional photography",
    "portrait": "detailed face, soft lighting, high quality, looking at camera, upper body portrait",
}

_IMAGE_GEN_CONFIG_CACHE: List[dict] = []
_IMAGE_GEN_CONFIG_CACHE_TS = 0.0
_IMAGE_GEN_CONFIG_CACHE_TTL_SECONDS = 300.0  # 优化：从30s增加到5分钟，减少高频批量生成时的DB查询


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


def _sign_volcano_request(
    method: str,
    url: str,
    body: bytes,
    access_key: str,
    secret_key: str,
    session_token: str = "",
    service: str = "ark",
    region: str = "cn-beijing",
) -> dict:
    """使用火山引擎 SignerV4 算法生成签名请求头（不依赖 volcengine SDK）"""
    import hashlib
    import hmac
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    format_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = format_date[:8]

    # Parse URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path if parsed.path else "/"

    headers = {
        "Host": host,
        "Content-Type": "application/json",
    }

    if session_token:
        headers["X-Security-Token"] = session_token

    # Compute body hash
    body_hash = hashlib.sha256(body).hexdigest()
    headers["X-Content-Sha256"] = body_hash
    headers["X-Date"] = format_date

    # Build signed headers
    signed_headers_dict = {}
    for key in headers:
        lower_key = key.lower()
        if lower_key in ["content-type", "content-md5", "host"] or lower_key.startswith("x-"):
            signed_headers_dict[lower_key] = headers[key]

    # Remove port from host if it's 80 or 443
    if "host" in signed_headers_dict:
        host_val = signed_headers_dict["host"]
        if ":" in host_val:
            parts = host_val.split(":")
            if parts[1] in ("80", "443"):
                signed_headers_dict["host"] = parts[0]

    signed_headers_str = ""
    for key in sorted(signed_headers_dict.keys()):
        signed_headers_str += key + ":" + signed_headers_dict[key] + "\n"

    signed_header_keys = ";".join(sorted(signed_headers_dict.keys()))

    canonical_request = "\n".join([
        method,
        path,
        "",
        signed_headers_str,
        signed_header_keys,
        body_hash,
    ])

    canonical_request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    credential_scope = "/".join([date_stamp, region, service, "request"])
    string_to_sign = "\n".join(["HMAC-SHA256", format_date, credential_scope, canonical_request_hash])

    # Derive signing key
    k_date = hmac.new(secret_key.encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = f"HMAC-SHA256 Credential={access_key}/{credential_scope}, SignedHeaders={signed_header_keys}, Signature={signature}"
    headers["Authorization"] = auth_header
    if session_token:
        headers["X-Security-Token"] = session_token
    return headers


def generate_image_with_volcano(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    config: Optional[dict] = None,
) -> Optional[str]:
    """使用火山引擎 (Ark) API 生成图片，返回图片 URL。
    优先使用 API Key (Bearer)，如果未配置则尝试 AK/SK 签名鉴权。
    """
    if config is None:
        configs = _get_active_image_gen_configs()
        for cfg in configs:
            if cfg.get("provider", "").lower() == "volcano":
                config = cfg
                break
    if not config:
        return None

    provider = config.get("provider", "")
    if provider.lower() != "volcano":
        return None

    api_key = config.get("api_key", "")
    access_key_id = config.get("access_key_id", "")
    secret_access_key = config.get("secret_access_key", "")
    session_token = config.get("session_token", "")
    model = config.get("model", "doubao-seedream-5-0-260128")
    base_url = config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3/images/generations")
    size = config.get("size", "2K")

    if not api_key and not (access_key_id and secret_access_key):
        logger.info("Volcano image generation skipped: missing API key and AK/SK")
        return None

    try:
        # 仅使用方舟文档列出的字段；勿传 negative_prompt 等未文档化参数，否则会 400。
        # 提示词过长也会触发 400（建议 ≤300 汉字或约 600 英文词）。
        prompt_clean = (prompt or "").strip()
        if len(prompt_clean) > 1800:
            prompt_clean = prompt_clean[:1800] + "..."

        payload = {
            "model": model,
            "prompt": prompt_clean,
            "sequential_image_generation": "disabled",
            "response_format": "url",
            "size": size,
            "stream": False,
            "watermark": False,
        }
        body_bytes = json.dumps(payload).encode("utf-8")

        if api_key:
            # 优先使用 Bearer Token (API Key) - 最稳定方式
            resp = requests.post(
                base_url,
                json=payload,  # 使用 json= 自动处理 headers 和 encoding
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=120,
            )
        else:
            # AK/SK 签名鉴权
            headers = _sign_volcano_request(
                method="POST",
                url=base_url,
                body=body_bytes,
                access_key=access_key_id,
                secret_key=secret_access_key,
                session_token=session_token,
            )
            resp = requests.post(base_url, data=body_bytes, headers=headers, timeout=120)

        resp.raise_for_status()
        data = resp.json()
        if data.get("data") and len(data["data"]) > 0:
            url = data["data"][0].get("url")
            if url:
                logger.info("Volcano image generation succeeded with URL: %s...", url[:60])
                return url
    except requests.exceptions.RequestException as e:
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                detail = (resp.text or "")[:1200]
            except Exception:
                pass
        logger.warning("Volcano HTTP request failed: %s %s", e, detail or "")
    except Exception as e:
        logger.warning("Volcano image generation failed: %s", e)

    return None
def generate_image_with_alibaba(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    config: Optional[dict] = None,
) -> Optional[str]:
    """使用阿里云百炼DashScope文生图API生成图片，返回图片临时URL。
    鉴权仅使用 DASHSCOPE_API_KEY Bearer 模式，无AK/SK签名。
    """
    # 1. 自动读取数据库中 provider=alibaba 的配置
    if config is None:
        configs = _get_active_image_gen_configs()
        for cfg in configs:
            if cfg.get("provider", "").lower() == "alibaba":
                config = cfg
                break
    if not config:
        return None

    provider = config.get("provider", "")
    if provider.lower() != "alibaba":
        return None

    # 2. 读取配置字段
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "qwen-image-2.0-pro")
    base_url = config.get("base_url", _ALIBABA_IMAGE_API_URL)
    timeout = config.get("timeout", 120)
    negative_prompt = config.get("negative_prompt", "").strip()

    # 校验密钥
    if not api_key:
        logger.info("Alibaba DashScope image generation skipped: missing DASHSCOPE_API_KEY")
        return None

    try:
        # 提示词超长截断，与火山逻辑统一
        prompt_clean = (prompt or "").strip()
        max_prompt_len = 1800
        if len(prompt_clean) > max_prompt_len:
            logger.warning(
                f"Alibaba prompt length {len(prompt_clean)} exceeds limit {max_prompt_len}, auto truncated"
            )
            prompt_clean = prompt_clean[:max_prompt_len] + "..."

        # 阿里百炼标准请求体（适配qwen-image-2.0-pro）
        payload = {
            "model": model,
            "input": {
                "prompt": prompt_clean
            },
            "parameters": {
                "size": f"{width}*{height}",
                "n": 1,
                "response_format": "url"
            }
        }
        # 存在负面提示词追加
        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt

        # Bearer鉴权请求
        resp = requests.post(
            base_url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        # 解析返回结构：output.results[0].url
        output = data.get("output", {})
        results = output.get("results", [])
        if not isinstance(results, list) or len(results) == 0:
            logger.warning("Alibaba API response empty results, no image generated")
            return None

        url = results[0].get("url")
        if not url:
            logger.warning("Alibaba generate success but missing image url")
            return None

        logger.info("Alibaba DashScope image generation succeeded with URL: %s...", url[:60])
        return url

    except requests.exceptions.RequestException as e:
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                detail = (resp.text or "")[:1200]
            except Exception:
                pass
        logger.warning("Alibaba DashScope HTTP request failed: %s %s", e, detail or "")
    except Exception as e:
        logger.warning("Alibaba DashScope image generation failed: %s", e)

    return None

def _get_cache_path(prompt: str) -> Path:
    """根据 prompt 生成缓存文件路径"""
    key = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    return IMAGE_CACHE_DIR / f"{key}.png"


def _is_valid_cache_file(path: Path, min_size: int = 1000) -> bool:
    """缓存文件存在且大小合理（避免命中损坏文件）"""
    try:
        return path.exists() and path.stat().st_size >= min_size
    except Exception:
        return False


# def _to_local_image_url(path: Path) -> str:
#     """将本地图片文件路径转换为项目可访问的静态 URL。确保返回有效路径。"""
#     if not path or not path.name:
#         return "https://picsum.photos/600/600?random=1"
#
#     return f"/data/images/{path.name}"

def _to_local_image_url(path: Path) -> str:
    """将本地图片文件路径转换为访问 URL。
    优先返回 COS URL，若 COS 未配置或上传失败则返回本地路径。
    注意：本地路径 /data/images/xxx 依赖 main.py 中的静态文件挂载。
    """
    if not path or not path.name:
        return "https://picsum.photos/600/600?random=1"

    # COS 已配置时，尝试上传并返回公网 URL
    if is_cos_enabled():
        cos_key = f"images/{path.name}"
        url = upload_file_to_cos(str(path), cos_key)
        if url:
            return url
        # COS 上传失败，记录日志并回退到本地路径（main.py 已挂载 /data/images）
        logger.warning("COS 上传失败，回退到本地路径: %s", path.name)

    return f"/data/images/{path.name}"

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


def _build_picsum_url(width: int = 600, height: int = 600) -> str:
    """构建 Picsum 随机图片 URL"""
    seed = random.randint(1, 100000)
    return f"https://picsum.photos/seed/{seed}/{width}/{height}"


def generate_image_with_xai(prompt: str, width: int = 1024, height: int = 1024) -> Optional[str]:
    """使用 xAI (Grok) API 生成图片，返回图片 URL。
    需要设置环境变量 XAI_API_KEY。
    """
    if not _XAI_API_KEY:
        logger.info("xAI image generation skipped: missing XAI_API_KEY")
        return None

    try:

        payload = json.dumps({
            "model": "grok-2-image",
            "prompt": prompt,
            "n": 1,
            "response_format": "url"
        }).encode("utf-8")

        req = urllib.request.Request(
            _XAI_IMAGE_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {_XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("data") and len(data["data"]) > 0:
                url = data["data"][0].get("url")
                if url:
                    logger.info("xAI image generation succeeded")
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
    """使用 xAI 生成图片并缓存到本地。返回本地缓存路径的 URL。
    如果缓存已存在，直接返回。
    如果 xAI 不可用，回退到 Pollinations.ai。
    """
    enhancement = _STYLE_ENHANCEMENTS.get(style, _STYLE_ENHANCEMENTS["anime"])
    full_prompt = f"{prompt}. {enhancement}"

    cache_key = f"xai|{style}|{width}x{height}|{full_prompt}"
    cache_path = _get_cache_path(cache_key)
    if _is_valid_cache_file(cache_path):
        url = _to_local_image_url(cache_path)
        if url and not ("/src/" in url or "main.tsx" in url):
            return url

    # 尝试 xAI
    xai_url = generate_image_with_xai(full_prompt, width, height)
    if xai_url:
        try:
            req = urllib.request.Request(xai_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                image_data = resp.read()
                with open(str(cache_path), "wb") as f:
                    f.write(image_data)
                if _is_valid_cache_file(cache_path):
                    return _to_local_image_url(cache_path)
            if _is_valid_cache_file(cache_path):
                url = _to_local_image_url(cache_path)
                if url and not ("/src/" in url or "main.tsx" in url):
                    return url
        except Exception as e:
            logger.warning("xAI image download failed: %s", e)

    # 回退到通用缓存管道
    logger.info("xAI generation unavailable, fallback to generic cache pipeline")
    return generate_image_with_cache(prompt, style, width, height)


def _download_and_upload_to_cos(url: str, filename: str = None) -> str:
    """下载外部图片并上传到COS，返回COS URL。
    如果下载或上传失败，返回原始URL。
    """
    import uuid
    import hashlib

    if not url or not url.startswith(('http://', 'https://')):
        return url

    # 生成文件名
    if not filename:
        url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:16]
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

        # 保存到本地缓存
        local_path = IMAGE_CACHE_DIR / filename
        with open(str(local_path), 'wb') as f:
            f.write(image_data)

        # 上传到COS
        cos_key = f"images/{filename}"
        cos_url = upload_file_to_cos(str(local_path), cos_key)
        if cos_url:
            logger.info("Downloaded and uploaded to COS: %s -> %s", url[:50], cos_url[:80])
            return cos_url

        # COS上传失败，返回本地路径
        logger.warning("COS upload failed for %s, returning local path", filename)
        return f"/data/images/{filename}"

    except Exception as e:
        logger.warning("Failed to download and upload image from %s: %s", url[:50], e)
        return url


def _generate_local_image(width: int = 600, height: int = 600) -> str:
    """当外部 API 全部失败时，下载真实照片并上传到COS。
    返回COS URL或本地路径，确保不依赖外部URL。
    """
    picsum_url = _build_picsum_url(width, height)
    return _download_and_upload_to_cos(picsum_url)


def generate_image(
    prompt: str,
    style: str = "",
    width: int = 600,
    height: int = 600,
    model: str = "",
    seed: Optional[int] = None,
    nologo: bool = True,
) -> str:
    """
    生成图片并返回本地缓存路径 URL（/data/images/...）。
    统一策略：无论上游提供商返回何种地址，都会先下载到本地缓存后再返回，
    避免前端继续依赖外部 URL。
    """
    return generate_image_with_cache(
        prompt=prompt,
        style=style or "anime",
        width=width,
        height=height,
        model=model,
        seed=seed,
        nologo=nologo,
    )


def generate_image_with_cache(
    prompt: str,
    style: str = "anime",
    width: int = 600,
    height: int = 600,
    model: str = "",
    seed: Optional[int] = None,
    nologo: bool = True,
) -> str:
    """
    生成图片并缓存到本地。返回本地缓存路径的 URL。
    如果缓存已存在，直接返回。
    注意：此方法会同步下载图片，可能耗时较长，建议在后台任务中使用。
    """
    enhancement = _STYLE_ENHANCEMENTS.get(style, _STYLE_ENHANCEMENTS["anime"])
    full_prompt = f"{prompt}. {enhancement}"

    cache_key = f"{style}|{width}x{height}|{model}|{seed}|{nologo}|{full_prompt}"
    cache_path = _get_cache_path(cache_key)
    if _is_valid_cache_file(cache_path):
        url = _to_local_image_url(cache_path)
        if url and not ("/src/" in url or "main.tsx" in url):
            return url
        # 缓存文件无效时继续生成

    # 主路径1：阿里云百炼生成 -> 下载落盘到本地缓存 -> 返回本地/COS URL
    configs = _get_active_image_gen_configs()
    alibaba_configs = [cfg for cfg in configs if cfg.get("provider", "").lower() == "alibaba"]
    for config in alibaba_configs:
        url = generate_image_with_alibaba(full_prompt, width, height, config=config)
        if not url:
            continue
        try:
            import requests
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            # 先保存到本地缓存（用于去重判断）
            with open(str(cache_path), "wb") as f:
                f.write(resp.content)
            if _is_valid_cache_file(cache_path):
                return _to_local_image_url(cache_path)
        except Exception as e:
            logger.warning("Alibaba image download failed: %s", e)


    # 主路径2：火山方舟生成 -> 下载落盘到指定本地目录 -> 返回本地 URL
    configs = _get_active_image_gen_configs()
    volcano_configs = [cfg for cfg in configs if cfg.get("provider", "").lower() == "volcano"]
    for config in volcano_configs:
        url = generate_image_with_volcano(full_prompt, width, height, config=config)
        if not url:
            continue
        try:
            import requests
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            # 先保存到本地缓存（用于去重判断）
            with open(str(cache_path), "wb") as f:
                f.write(resp.content)
            if _is_valid_cache_file(cache_path):
                return _to_local_image_url(cache_path)
        except Exception as e:
            logger.warning("Volcano image download failed: %s", e)

    # 回退：使用高质量 picsum 真实照片，确保朋友圈始终可用且自然
    # 避免复杂 copy 逻辑和潜在的本地文件不一致问题
    local_url = _generate_local_image(width, height)
    logger.info("All upstream image providers failed, using reliable picsum real-photo fallback")
    return local_url


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
    gender_en = "female" if gender == "女" else "male"
    age = profile.get("age", 22)
    city = profile.get("city", "")
    personality = profile.get("personality", "")
    background = profile.get("background", "")
    hobbies = profile.get("hobbies", "")
    mbti = profile.get("mbti", "")
    values = profile.get("values", "")
    favorite_things = profile.get("favorite_things", "")
    speech_style = profile.get("speech_style", "")

    # ---------- 1. 收集人设特征描述 ----------
    trait_parts = []
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

    # ---------- 3. 年龄外观描述 ----------
    if age <= 20:
        age_desc = "青春洋溢，面带纯真"
    elif age <= 24:
        age_desc = "年轻活力，朝气蓬勃"
    elif age <= 28:
        age_desc = "成熟优雅，气质出众"
    else:
        age_desc = "沉稳内敛，魅力十足"

    # ---------- 4. 根据风格生成 prompt ----------
    if style == "realistic":
        prompt = f"Professional portrait photograph of a {age}-year-old {gender_en}"
        if city:
            prompt += f" from {city}"
        prompt += (
            f". {character_info}. {age_desc}"
        )
        if mbti_vibe:
            prompt += f". {mbti_vibe}"
        prompt += (
            ". Natural soft lighting, golden hour glow, shallow depth of field, "
            "photorealistic, 8k ultra high quality, detailed skin texture, "
            "professional DSLR photography, subtle bokeh background, "
            "upper body portrait, looking directly at camera, natural gentle expression, "
            "sharp focus on eyes, cinematic color grading, flattering angle"
        )
    else:
        # anime 风格（默认）
        prompt = f"Stunning anime portrait of a {age}-year-old {gender_en} character"
        if city:
            prompt += f" from {city}"
        prompt += (
            f". {character_info}. {age_desc}"
        )
        if mbti_vibe:
            prompt += f". {mbti_vibe}"
        prompt += (
            ". Large expressive detailed eyes, delicate beautiful facial features, "
            "soft pastel color palette, clean smooth line art, "
            "masterpiece illustration quality, soft gradient or minimal background, "
            "beautiful lighting on face, best quality, highly detailed, "
            "professional anime artwork, studio quality"
        )

    return prompt


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
    gender_en = "young woman" if gender == "女" else "young man"
    age = int(profile.get("age", 22) or 22)
    personality = (profile.get("personality", "") or "").strip()[:80]
    hobbies = (profile.get("hobbies", "") or "").strip()[:60]
    city = (profile.get("city", "") or "").strip()
    mbti = (profile.get("mbti", "") or "").strip()

    key_trait = "warm and authentic"
    if personality:
        parts = [x.strip() for x in personality.replace("，", ",").replace("、", ",").split(",") if x.strip()]
        if parts:
            key_trait = parts[0][:40]

    loc_hint = f"Atmosphere fits someone living in or loving {city}. " if city else ""
    hobby_hint = f"Tastes influenced by hobbies: {hobbies}. " if hobbies else ""
    mbti_hint = f"Subtle vibe hint: {mbti}. " if mbti else ""

    persona_lock = (
        f"RECURRING SAME CHARACTER if any face is shown: single {age}-year-old {gender_en}, "
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

    # person in frame
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
            "pose and expression matching caption mood; natural skin, smartphone or mirror-selfie realism, "
            "shallow depth of field, believable daily life. "
            f"{common_tail}"
        )
    else:
        p = (
            f"Anime / illustration social-feed image for post: {cap}. "
            f"{persona_lock}"
            f"{loc_hint}{hobby_hint}{mbti_hint}"
            f"Same recurring {age}-year-old {gender_en} character{location}, expressive eyes, clean line art, "
            f"scene and outfit matching caption; personality reads as {key_trait}. "
            f"{common_tail}"
        )
    return (p, style)
