"""朋友圈：读取 companions 人设，头像传视觉 LLM 做人脸一致，配图贴合国家/语言风格。"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from langchain_core.messages import HumanMessage

from core.config import BASE_DIR
from core.i18n import normalize_ui_language
from services.agent import get_llm
from services.agent_utils import llm_content_to_str
from services.culture_data import infer_language_from_city
from services.image_generation import _moment_caption_route, generate_moment_image_prompt
from services.region_catalog import find_region_for_city

logger = logging.getLogger(__name__)

_MAX_AVATAR_BYTES = 4 * 1024 * 1024

# 各语言社交动态平台与语气参考（配图/文案本地化）
_LOCALE_FEED_STYLE: Dict[str, str] = {
    "zh": "中国本土朋友圈/小红书风格：口语化、生活化细节（奶茶、外卖、地铁、商场、季节天气），避免翻译腔。",
    "en": "Western Instagram/Twitter feed: casual first-person, local cafes, commute, weekend vibes; native English only.",
    "ja": "日本のLINE/Instagram風：控えめだが親しみやすい、コンビニ・電車・カフェ・季節の話題。自然な日本語のみ。",
    "ko": "한국 카카오스토리/인스타 감성: 일상 디테일, 카페·지하철·배달·날씨, 자연스러운 한국어만.",
    "pt": "Feed estilo Instagram/WhatsApp Brasil/Portugal: tom casual, comida local, clima, vida urbana; português nativo.",
    "es": "Feed tipo Instagram en español: cotidiano, comida, clima, barrio; español natural del país.",
    "id": "Gaya feed Instagram/WhatsApp Indonesia: santai, kuliner lokal, ojek online, cuaca tropis; Bahasa Indonesia asli.",
}

_COUNTRY_VISUAL_HINTS: Dict[str, str] = {
    "CN": "Chinese urban/suburban aesthetics, local signage feel, East Asian street life",
    "HK": "Hong Kong dense city, neon, cha chaan teng, harbor skyline",
    "TW": "Taiwan night markets, scooters, bubble tea, humid subtropical light",
    "JP": "Japanese streets, konbini, clean signage, seasonal atmosphere",
    "KR": "Korean cafe culture, apartment blocks, neon, clean streets",
    "US": "American suburbs or city blocks, diners, cars, wide sidewalks",
    "GB": "UK pubs, grey skies, brick terraces, tea culture",
    "BR": "Brazilian tropical light, colorful streets, beach or favela-adjacent urban",
    "ID": "Indonesian tropical humidity, warung, motorbikes, lush greenery",
    "ES": "Mediterranean light, tapas bars, old town alleys",
    "PT": "Iberian tiles, trams, Atlantic coast light",
    "FR": "Parisian or provincial French cafe terraces, soft golden hour",
    "DE": "German orderly streets, bakeries, trams, cool northern light",
    "SEA": "Southeast Asian tropical urban, hawker centers, humid warmth",
}


def _valid_avatar_url(avatar_url: Optional[str]) -> str:
    u = (avatar_url or "").strip()
    if not u or u == "__GENERATING__":
        return ""
    return u


def _profile_source(source: Any) -> Any:
    """Companion（含 .profile）或 CompanionORM 行。"""
    return getattr(source, "profile", source)


def build_moment_profile(source: Any) -> Dict[str, Any]:
    """从 companions 表 / Companion 对象汇总发圈用人设（完整配置）。"""
    p = _profile_source(source)
    city = (getattr(p, "city", None) or "").strip()
    lang_raw = (
        (getattr(p, "language", None) or "").strip()
        or infer_language_from_city(city)
        or "zh"
    )
    lang = normalize_ui_language(lang_raw.split("-")[0].strip() or "zh")

    country = (getattr(p, "country", None) or "").strip().upper()
    region_key = (getattr(p, "region_key", None) or "").strip()
    region_label = (getattr(p, "region_label", None) or "").strip()

    if city and (not region_key or not region_label):
        reg = find_region_for_city(city, lang)
        if reg:
            region_key = region_key or (reg.get("key") or "")
            region_label = region_label or (reg.get("label") or "")
            if not country and reg.get("country"):
                country = str(reg["country"]).upper()

    return {
        "name": getattr(p, "name", "") or "",
        "gender": getattr(p, "gender", "") or "",
        "age": getattr(p, "age", None) or 22,
        "city": city,
        "country": country,
        "region_key": region_key,
        "region_label": region_label,
        "language": lang,
        "personality": (getattr(p, "personality", None) or "").strip(),
        "background": (getattr(p, "background", None) or "").strip(),
        "description": (getattr(p, "description", None) or "").strip(),
        "speech_style": (getattr(p, "speech_style", None) or "").strip(),
        "appearance": (getattr(p, "appearance", None) or "").strip(),
        "hobbies": (getattr(p, "hobbies", None) or "").strip(),
        "values": (getattr(p, "values", None) or "").strip(),
        "cultural_values": (getattr(p, "cultural_values", None) or "").strip(),
        "daily_routine": (getattr(p, "daily_routine", None) or "").strip(),
        "favorite_things": (getattr(p, "favorite_things", None) or "").strip(),
        "love_view": (getattr(p, "love_view", None) or "").strip(),
        "mbti": (getattr(p, "mbti", None) or "").strip(),
        "life_story": (getattr(p, "life_story", None) or "").strip()[:200],
        "avatar_url": (getattr(p, "avatar_url", None) or "").strip(),
    }


def locale_context_for_moments(profile: Dict[str, Any]) -> str:
    """国家/地区 + 语言对应的社交动态风格说明（注入文案与配图 prompt）。"""
    lang = profile.get("language") or "zh"
    city = profile.get("city") or ""
    country = (profile.get("country") or "").upper()
    region_label = profile.get("region_label") or ""
    cultural = (profile.get("cultural_values") or "")[:200]
    speech = (profile.get("speech_style") or "")[:120]

    feed_style = _LOCALE_FEED_STYLE.get(lang, _LOCALE_FEED_STYLE["en"])
    visual = _COUNTRY_VISUAL_HINTS.get(country, "")
    if not visual and region_label:
        visual = f"Regional look of {region_label}"

    parts = [
        f"Language: {lang} (caption MUST be in this language only).",
        f"Location: {city}" + (f", {region_label}" if region_label else "") + (f", country {country}" if country else ""),
        f"Feed tone: {feed_style}",
    ]
    if visual:
        parts.append(f"Visual/local culture: {visual}")
    if cultural:
        parts.append(f"Cultural values: {cultural}")
    if speech:
        parts.append(f"Speech style: {speech}")
    return "\n".join(parts)


def moment_persona_block(companion: Any) -> str:
    """供文案 LLM 使用的人设与国家风格块。"""
    prof = build_moment_profile(companion)
    hobbies = prof.get("hobbies") or ""
    appearance = prof.get("appearance") or ""
    values = prof.get("values") or ""
    locale = locale_context_for_moments(prof)

    extra = []
    if appearance:
        extra.append(f"外貌：{appearance[:120]}")
    if hobbies:
        extra.append(f"爱好：{hobbies[:100]}")
    if values:
        extra.append(f"价值观：{values[:100]}")

    block = locale
    if extra:
        block += "\n" + "\n".join(extra)
    return block


def profile_dict_from_companion(companion) -> Dict[str, Any]:
    """配图管线用的人设 dict（与 build_moment_profile 一致）。"""
    return build_moment_profile(companion)


def fetch_avatar_bytes(avatar_url: Optional[str]) -> Optional[Tuple[bytes, str]]:
    """下载或读取智能体头像，返回 (bytes, mime)。"""
    url = _valid_avatar_url(avatar_url)
    if not url:
        return None

    try:
        if url.startswith(("http://", "https://")):
            resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.content
            # 只要下载成功且非空就可用；不再因“文件偏小”回退
            if not data or len(data) > _MAX_AVATAR_BYTES:
                return None
            ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            return data, ctype or "image/jpeg"

        candidates = []
        if url.startswith("/"):
            candidates.append(BASE_DIR / url.lstrip("/"))
            candidates.append(Path(url))
        else:
            candidates.append(BASE_DIR / url)
            candidates.append(BASE_DIR / "data" / "images" / url)

        for path in candidates:
            if path.is_file():
                data = path.read_bytes()
                # 本地头像同样只要求可读且非空
                if not data or len(data) > _MAX_AVATAR_BYTES:
                    return None
                suffix = path.suffix.lower()
                mime = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                }.get(suffix, "image/jpeg")
                return data, mime
    except Exception as e:
        logger.warning("[MomentImage] fetch avatar failed url=%s: %s", url[:80], e)
    return None


def _bytes_to_data_url(data: bytes, mime: str) -> str:
    b64 = base64.standard_b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _extract_json_block(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _vision_llm():
    for provider in ("google", "grok", "deepseek"):
        try:
            llm = get_llm(temperature=0.35, max_tokens=900, provider=provider)
            if llm:
                return llm
        except Exception:
            continue
    return get_llm(temperature=0.35, max_tokens=900)


def _build_vision_user_parts(
    caption: str,
    profile: Dict[str, Any],
    avatar_data_url: str,
) -> list:
    locale = locale_context_for_moments(profile)
    name = profile.get("name") or "companion"
    age = profile.get("age") or 22
    gender = profile.get("gender") or ""
    personality = (profile.get("personality") or "")[:120]
    appearance = (profile.get("appearance") or "")[:160]
    hobbies = (profile.get("hobbies") or "")[:80]

    instruction = f"""You create image-generation prompts for a localized social-media feed post.

REFERENCE AVATAR (attached image): this is the character's official face. When the person appears in the photo, you MUST preserve the SAME face identity — face shape, eyes, nose, lips, skin tone, hair color/style, age appearance. This is face-tracking / identity lock from the reference.

Post caption: "{caption}"

Character profile (from companions DB):
- Name: {name}, age {age}, gender {gender}
- Personality: {personality}
- Appearance notes: {appearance or "match avatar"}
- Hobbies: {hobbies or "n/a"}

Locale & visual culture (MUST follow):
{locale}

Steps:
1. Decide shows_self: true if caption implies selfie, mirror, outfit, "I/me", or the person in scene; false for pure scenery/food/objects.
2. Write ONE English image prompt (90-150 words):
   - If shows_self=true: candid smartphone photo of THE SAME PERSON as the avatar; outfit/scene match caption; local architecture/food/fashion from locale above; natural light.
   - If shows_self=false: scenery/still life matching caption and locale; no prominent face (tiny distant silhouettes OK).
3. style: "realistic" for photo-like, "anime" only if hobbies/personality clearly favor illustration.

Reply JSON ONLY:
{{"shows_self": true|false, "style": "realistic"|"anime", "prompt": "..."}}"""

    return [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": avatar_data_url}},
    ]


def generate_moment_image_plan_with_avatar(
    caption: str,
    profile: Optional[Dict[str, Any]],
    avatar_url: Optional[str],
    *,
    lang: str = "zh",
) -> Tuple[str, str, bool]:
    """
    读取 companions 人设 + 头像 → 视觉 LLM 做人脸一致与本地化配图 prompt。

    Returns:
        (prompt, style, used_vision)
    """
    profile = dict(profile or {})
    if lang and not profile.get("language"):
        profile["language"] = normalize_ui_language(lang)

    avatar = fetch_avatar_bytes(avatar_url or profile.get("avatar_url"))
    route = _moment_caption_route(caption)

    if not avatar:
        prompt, style = generate_moment_image_prompt(caption, profile=profile)
        return prompt, style, False

    # 纯风景文案：不传头像，但 prompt 仍带国家风格
    if route == "scenery":
        prompt, style = generate_moment_image_prompt(caption, profile=profile)
        return prompt, style, False

    data_url = _bytes_to_data_url(avatar[0], avatar[1])
    try:
        llm = _vision_llm()
        resp = llm.invoke([HumanMessage(content=_build_vision_user_parts(caption, profile, data_url))])
        raw = llm_content_to_str(getattr(resp, "content", ""))
        parsed = _extract_json_block(raw)
        if parsed and isinstance(parsed.get("prompt"), str):
            prompt = parsed["prompt"].strip()
            style = (parsed.get("style") or "realistic").strip().lower()
            if style not in ("realistic", "anime"):
                style = "realistic"
            shows_self = bool(parsed.get("shows_self"))
            if prompt and len(prompt) >= 40:
                locale_tail = locale_context_for_moments(profile).split("\n")[0]
                if shows_self:
                    prompt = (
                        f"{prompt} "
                        "FACE IDENTITY LOCK: identical person to reference avatar — same face, hair, skin tone. "
                        f"{locale_tail} "
                        "No text overlays, no watermark."
                    )
                else:
                    prompt = f"{prompt} {locale_tail} No text overlays, no watermark."
                logger.info(
                    "[MomentImage] vision plan shows_self=%s lang=%s country=%s",
                    shows_self,
                    profile.get("language"),
                    profile.get("country"),
                )
                return prompt[:1800], style, True
    except Exception as e:
        logger.warning("[MomentImage] vision LLM failed, fallback: %s", e)

    prompt, style = generate_moment_image_prompt(caption, profile=profile)
    return prompt, style, False


def generate_moment_image_bytes(companion_id: str, caption: str) -> Tuple[bytes, str]:
    """根据智能体人设 + 文案生成朋友圈配图，返回 (图片 bytes, media_type)。不落库、不上传 COS。"""
    from core.database import CompanionORM, get_db
    from services.image_generation import ImageGenerationError, generate_image_bytes

    cid = (companion_id or "").strip()[:8]
    text = (caption or "").strip()
    if not cid:
        raise ValueError("companion_not_found")
    if not text:
        raise ValueError("caption_required")

    with get_db() as db:
        companion = db.query(CompanionORM).filter(CompanionORM.id == cid).first()
        if not companion:
            raise ValueError("companion_not_found")
        profile_dict = build_moment_profile(companion)

    avatar_url = profile_dict.get("avatar_url") or ""
    lang = profile_dict.get("language") or "zh"
    img_prompt, img_style, _used_vision = generate_moment_image_plan_with_avatar(
        text,
        profile_dict,
        avatar_url,
        lang=lang,
    )
    try:
        return generate_image_bytes(img_prompt, style=img_style, width=600, height=600)
    except ImageGenerationError:
        raise
    except Exception as e:
        raise ImageGenerationError(str(e)) from e
