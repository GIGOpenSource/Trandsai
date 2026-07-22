import hashlib
import json
import logging
import os
import random
import time
import urllib.parse
from pathlib import Path
from typing import List, Optional, Tuple
from dotenv import load_dotenv
import requests  # 统一在顶部导入，避免函数内重123复 impor123t
load_dotenv()
from core.config import BASE_DIR

from services.cos_storage import is_cos_enabled, upload_file_to_cos, upload_bytes_to_cos
logger = logging.getLogger(__name__)

# xAI API 配置
_XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
_XAI_IMAGE_API_URL = "https://api.x.ai/v1/images/generations"

# 阿里云百炼 DashScope 文生图配置
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


def generate_image_with_xai(prompt: str, width: int = 1024, height: int = 1024, config: dict = None) -> Optional[str]:
    """使用 xAI (Grok) API 生成图片，返回图片 URL。
    优先从 config 读取 api_key，否则回退到环境变量 XAI_API_KEY。
    """
    api_key = (config or {}).get("api_key") or _XAI_API_KEY
    if not api_key:
        logger.info("xAI image generation skipped: missing XAI_API_KEY")
        return None

    model = (config or {}).get("model", "grok-imagine-image-quality")
    base_url = (config or {}).get("base_url", _XAI_IMAGE_API_URL)

    try:

        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "n": 1,
            "response_format": "url"
        }).encode("utf-8")

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


def _download_and_upload_to_cos(url: str, filename: str = None) -> str:
    """下载外部图片并上传到COS，返回COS URL。
    如果下载或上传失败，返回原始URL。
    """
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
    """当外部 API 全部失败时，返回空字符串。
    不再依赖 picsum.photos 等外部随机图服务。
    """
    logger.warning("所有图片生成服务均失败，无可用的回退方案")
    return ""


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
    生成图片并返回COS URL。
    统一策略：无论上游提供商返回何种地址，都会上传到COS后返回。
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
    生成图片并上传到COS，返回COS URL。
    注意：此方法会同步下载图片，可能耗时较长，建议在后台任务中使用。
    """
    enhancement = _STYLE_ENHANCEMENTS.get(style, _STYLE_ENHANCEMENTS["anime"])
    full_prompt = f"{prompt}. {enhancement}"

    # 主路径1：阿里云百炼生成 -> 上传到COS -> 返回COS URL
    configs = _get_active_image_gen_configs()
    alibaba_configs = [cfg for cfg in configs if cfg.get("provider", "").lower() == "alibaba"]
    for config in alibaba_configs:
        url = generate_image_with_alibaba(full_prompt, width, height, config=config)
        if not url:
            continue
        cos_url = _download_and_upload_to_cos(url)
        if cos_url and cos_url.startswith("http"):
            return cos_url

    # 主路径2：火山方舟生成 -> 上传到COS -> 返回COS URL
    configs = _get_active_image_gen_configs()
    volcano_configs = [cfg for cfg in configs if cfg.get("provider", "").lower() == "volcano"]
    for config in volcano_configs:
        url = generate_image_with_volcano(full_prompt, width, height, config=config)
        if not url:
            continue
        cos_url = _download_and_upload_to_cos(url)
        if cos_url and cos_url.startswith("http"):
            return cos_url

    # 主路径3：xAI (Grok) 生成 -> 上传到COS -> 返回COS URL
    xai_api_key = _XAI_API_KEY
    xai_config = None
    configs = _get_active_image_gen_configs()
    xai_configs = [cfg for cfg in configs if cfg.get("provider", "").lower() == "xai"]
    if xai_configs:
        xai_config = xai_configs[0]
    if xai_config or xai_api_key:
        url = generate_image_with_xai(full_prompt, width, height, config=xai_config)
        if url:
            cos_url = _download_and_upload_to_cos(url)
            if cos_url and cos_url.startswith("http"):
                return cos_url

    # 所有 AI 服务均失败，返回空字符串
    logger.warning("所有图片生成服务均失败")
    return ""


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
