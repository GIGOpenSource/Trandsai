"""IM 译文：优先 Google Cloud Translation，失败回退百度翻译。

目标语言由 Accept-Language 映射。对外仍返回百度风格 to 码
（ja→jp、ko→kor、zh-TW→cht），调用 Google 时再转成 Google 语言码。
文档：https://docs.cloud.google.com/translate/docs
"""
from __future__ import annotations

import hashlib
import html
import logging
import os
import random
import re
import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# 只用 HTTPS。HTTP 在海外节点上常会挂死到 timeout，叠多个地址最多 30s，
# 网关掐连接后浏览器表现为 Failed to fetch（偶现）。
_GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"
_BAIDU_URLS = (
    "https://fanyi-api.baidu.com/api/trans/vip/translate",
    "https://api.fanyi.baidu.com/api/trans/vip/translate",
)
_BAIDU_RETRYABLE = frozenset({"52001", "52002", "54003", "54005"})
_HAN = re.compile(r"[\u4e00-\u9fff]")
_STRIP = re.compile(
    r"[\s\U0001F300-\U0001F9FF\u2600-\u27BF\uFE0F\u200D]+",
    flags=re.UNICODE,
)

# Accept-Language 主语言 → 百度开放平台 to 码（https://fanyi-api.baidu.com/doc/21）
# 注意：没有 cn；日语是 jp 不是 ja；韩语是 kor 不是 ko；西语是 spa；法语是 fra。
_BAIDU_FROM_BASE = {
    "zh": "zh",
    "cn": "zh",
    "en": "en",
    "ja": "jp",
    "jp": "jp",
    "ko": "kor",
    "kor": "kor",
    "fr": "fra",
    "fra": "fra",
    "es": "spa",
    "spa": "spa",
    "pt": "pt",
    "de": "de",
    "it": "it",
    "ru": "ru",
    "th": "th",
    "vi": "vie",
    "vie": "vie",
    "ar": "ara",
    "ara": "ara",
    "id": "id",
    "nl": "nl",
    "pl": "pl",
    "cs": "cs",
    "el": "el",
    "hu": "hu",
    "sv": "swe",
    "swe": "swe",
    "da": "dan",
    "dan": "dan",
    "fi": "fin",
    "fin": "fin",
    "ro": "rom",
    "rom": "rom",
    "tr": "tr",
    "bg": "bul",
    "bul": "bul",
    "ir": "per",
    "fa": "per",
    "per": "per",
    "cht": "cht",
}

_LATIN_BAIDU = frozenset(
    {
        "en",
        "fra",
        "spa",
        "pt",
        "de",
        "it",
        "id",
        "nl",
        "pl",
        "cs",
        "el",
        "hu",
        "swe",
        "dan",
        "fin",
        "rom",
    }
)

# 同属拉丁字母的语种不能靠「有没有 ABC」判断是否已是目标语（西语/法语/英语都会被当成 en）
_LATIN_STOP = {
    "en": frozenset(
        "the a an is are was were be been being you your i we they this that of and to "
        "for with it on at as from or by not have has had do does did how what when my "
        "me his her their our but if so can will just about into than then".split()
    ),
    "spa": frozenset(
        "el la los las un una de y que en es por con no se del al te tu tú cómo como "
        "está estado ha he sido para más pero lo le les mi mis su sus yo qué muy ya "
        "si porque sobre también hay fue son o hasta entre".split()
    ),
    "fra": frozenset(
        "le la les un une de et que en est pas pour avec je tu il elle des du au aux "
        "ce cette dans qui ne nous vous sur plus par mais où comment".split()
    ),
    "pt": frozenset(
        "o a os as um uma de e que em é para com não se do da dos das você seu sua "
        "como está foi por mais mas eu ele ela nos nós já também".split()
    ),
    "de": frozenset(
        "der die das den dem ein eine und ist ich du sie wir nicht mit auf für von "
        "zu im den des das auch wie nur noch nach bei oder".split()
    ),
    "it": frozenset(
        "il lo la i gli le un uno una di e che in è per con non si del della dei "
        "delle degli a da su come più ma anche".split()
    ),
    "id": frozenset(
        "yang dan di itu ini tidak saya kamu dia untuk dengan pada dari atau ada "
        "sudah akan bisa kami mereka ke dalam".split()
    ),
    "nl": frozenset(
        "de het een en van ik je hij zij wij niet met op voor aan te dat die is "
        "was zijn naar ook maar".split()
    ),
}

_CACHE_MAX = 2048
# (to_lang, q) → (dst, provider)  provider = google | baidu
_cache: "OrderedDict[tuple[str, str], tuple[str, str]]" = OrderedDict()
_cache_lock = threading.Lock()

DEFAULT_BAIDU_TO = "zh"

# 百度 to 码 → Google Cloud Translation target（BCP-47）
_BAIDU_TO_GOOGLE = {
    "zh": "zh-CN",
    "cht": "zh-TW",
    "jp": "ja",
    "kor": "ko",
    "fra": "fr",
    "spa": "es",
    "vie": "vi",
    "ara": "ar",
    "swe": "sv",
    "dan": "da",
    "fin": "fi",
    "rom": "ro",
    "bul": "bg",
    "per": "fa",
}


def _baidu_appid() -> str:
    return (os.getenv("BAIDU_TRANSLATE_APPID") or "20251120002501888").strip()


def _baidu_secret() -> str:
    return (os.getenv("BAIDU_TRANSLATE_SECRET") or "sNfan88yyvUeUNtfIAkm").strip()


def baidu_to_lang_from_accept(accept_language: Optional[str]) -> str:
    """把 Accept-Language 映射成百度 `to`。缺省 / 无法识别 → zh。

    前端应传 BCP-47，例如 zh-CN、en-US、ja-JP。不要传 cn（不是标准码，仅作兼容）。
    """
    raw = (accept_language or "").split(",")[0].strip().split(";")[0].strip()
    raw = raw.replace("_", "-")
    lower = raw.lower()
    if not lower:
        return DEFAULT_BAIDU_TO

    if (
        lower.startswith("zh-hant")
        or lower.startswith("zh-tw")
        or lower.startswith("zh-hk")
        or lower.startswith("zh-mo")
    ):
        return "cht"
    if lower in ("cn", "zh", "zh-cn", "zh-hans", "zh-sg") or lower.startswith("zh") or lower.startswith("cn-"):
        return "zh"

    base = lower.split("-")[0]
    return _BAIDU_FROM_BASE.get(base, DEFAULT_BAIDU_TO)


_WORD_RE = re.compile(r"[A-Za-zÀ-ÿĀ-ž]+", re.UNICODE)


def _latin_already_target(text: str, to_lang: str) -> bool:
    """拉丁字母语种：用停用词判断是否已是目标语。无证据则不当成已是目标语。"""
    stops = _LATIN_STOP.get((to_lang or "").lower())
    if not stops:
        return False
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    if not words:
        return False
    scores = {
        lang: sum(1 for w in words if w in vocab)
        for lang, vocab in _LATIN_STOP.items()
    }
    target_score = int(scores.get(to_lang, 0) or 0)
    if target_score <= 0:
        return False
    best_lang = max(scores, key=lambda k: scores[k])
    best_score = int(scores[best_lang] or 0)
    if best_lang != to_lang and best_score > target_score:
        return False
    return target_score >= max(1, len(words) // 16)


def needs_translation(text: str, to_lang: str = DEFAULT_BAIDU_TO) -> bool:
    """目标语已经占主导则不调接口。拉丁语种之间不能只靠字母表判断。"""
    raw = (text or "").strip()
    if not raw:
        return False
    if not _STRIP.sub("", raw):
        return False
    latin = len(re.findall(r"[A-Za-zÀ-ÿ]", raw))
    kana = len(re.findall(r"[\u3040-\u30ff]", raw))
    hangul = len(re.findall(r"[\uac00-\ud7af]", raw))
    han = len(_HAN.findall(raw))
    to_lang = (to_lang or DEFAULT_BAIDU_TO).lower()

    if to_lang in ("zh", "cht"):
        # 日语汉字、韩文汉字不能当成中文 skip
        if kana or hangul:
            return True
        if han == 0:
            return True
        foreign = latin + kana + hangul
        return foreign > han * 2

    if to_lang == "jp":
        other = latin + hangul + han
        if kana == 0:
            return True
        if other == 0:
            return False
        return other > kana * 2

    if to_lang == "kor":
        other = latin + kana + han
        if hangul == 0:
            return True
        if other == 0:
            return False
        return other > hangul * 2

    if to_lang in _LATIN_BAIDU:
        other = han + kana + hangul
        if latin == 0:
            return True
        if other > latin * 2:
            return True
        # 西语/法语/英语同属拉丁字母：不能因为有 ABC 就当成已经是英语
        return not _latin_already_target(raw, to_lang)

    return True


def needs_zh_translation(text: str) -> bool:
    return needs_translation(text, "zh")


def _cache_get(to_lang: str, q: str) -> Optional[Tuple[str, str]]:
    key = (to_lang, q)
    with _cache_lock:
        val = _cache.get(key)
        if val is not None:
            _cache.move_to_end(key)
        return val


def _cache_put(to_lang: str, q: str, dst: str, provider: str) -> None:
    key = (to_lang, q)
    item = (dst, provider)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            _cache[key] = item
            return
        _cache[key] = item
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def _google_api_key() -> str:
    key = (
        os.getenv("GOOGLE_TRANSLATE_API_KEY")
        or os.getenv("GOOGLE_CLOUD_TRANSLATE_API_KEY")
        or ""
    ).strip()
    if key:
        return key
    try:
        from pathlib import Path
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parents[2] / ".env"
        if root.is_file():
            load_dotenv(dotenv_path=root, override=False)
        key = (
            os.getenv("GOOGLE_TRANSLATE_API_KEY")
            or os.getenv("GOOGLE_CLOUD_TRANSLATE_API_KEY")
            or ""
        ).strip()
    except Exception:
        return ""
    return key


def google_target_from_to_lang(to_lang: str) -> str:
    """百度 to 码或前端短码 → Google target。"""
    raw = (to_lang or DEFAULT_BAIDU_TO).strip() or DEFAULT_BAIDU_TO
    lower = raw.lower().replace("_", "-")
    if lower in ("zh-cn", "zh-hans"):
        return "zh-CN"
    if lower in ("zh-tw", "zh-hk", "zh-mo", "zh-hant"):
        return "zh-TW"
    mapped = _BAIDU_TO_GOOGLE.get(lower)
    if mapped:
        return mapped
    base = _BAIDU_FROM_BASE.get(lower.split("-")[0], "")
    if base and base in _BAIDU_TO_GOOGLE:
        return _BAIDU_TO_GOOGLE[base]
    if lower.split("-")[0] in _BAIDU_FROM_BASE:
        code = _BAIDU_FROM_BASE[lower.split("-")[0]]
        return _BAIDU_TO_GOOGLE.get(code, lower)
    return raw


def _google_translate(q: str, to_lang: str, from_lang: str = "auto") -> Optional[str]:
    key = _google_api_key()
    if not key:
        logger.warning("im_translate: GOOGLE_TRANSLATE_API_KEY 未配置，跳过 Google")
        return None
    target = google_target_from_to_lang(to_lang)
    payload = {"q": q, "target": target, "format": "text"}
    src = (from_lang or "auto").strip()
    if src and src.lower() != "auto":
        payload["source"] = google_target_from_to_lang(src)
    try:
        resp = requests.post(
            _GOOGLE_TRANSLATE_URL,
            params={"key": key},
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=(3, 12),
        )
        data = resp.json() if resp.content else {}
        rows = (data.get("data") or {}).get("translations") or []
        if rows and isinstance(rows[0], dict) and rows[0].get("translatedText"):
            return html.unescape(str(rows[0]["translatedText"]))
        err = data.get("error") or {}
        logger.warning(
            "im_translate google error http=%s code=%s msg=%s",
            resp.status_code,
            err.get("code"),
            err.get("message") or data,
        )
    except requests.Timeout:
        logger.warning("im_translate google timeout")
    except Exception as e:
        logger.warning("im_translate google request failed: %s", e)
    return None


def _baidu_translate(q: str, to_lang: str, from_lang: str = "auto") -> Optional[str]:
    appid = _baidu_appid()
    secret = _baidu_secret()
    if not appid or not secret:
        logger.warning("im_translate: BAIDU_TRANSLATE_APPID/SECRET 未配置")
        return None
    salt = random.randint(32768, 65536)
    sign = hashlib.md5(f"{appid}{q}{salt}{secret}".encode("utf-8")).hexdigest()
    params = urlencode(
        {
            "q": q,
            "from": from_lang,
            "to": to_lang,
            "appid": appid,
            "salt": salt,
            "sign": sign,
        }
    )
    last_err = None

    for attempt in range(2):
        for base in _BAIDU_URLS:
            try:
                resp = requests.get(
                    f"{base}?{params}",
                    timeout=(2, 5),
                    allow_redirects=True,
                )
                raw = (resp.text or "").strip()
                if resp.status_code >= 400 or not raw:
                    last_err = f"http={resp.status_code} empty={not bool(raw)}"
                    logger.warning("im_translate baidu skip %s %s", base, last_err)
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    last_err = f"http={resp.status_code} non-json"
                    logger.warning(
                        "im_translate baidu skip %s %s body=%s",
                        base,
                        last_err,
                        raw[:120],
                    )
                    continue
                rows = data.get("trans_result") or []
                if rows and isinstance(rows[0], dict) and rows[0].get("dst"):
                    return str(rows[0]["dst"])
                err_code = str(data.get("error_code") or "")
                last_err = f"{err_code} {data.get('error_msg')}"
                logger.warning("im_translate baidu api error %s", last_err)
                if err_code in _BAIDU_RETRYABLE and attempt == 0:
                    time.sleep(0.25)
                    break
                return None
            except requests.Timeout as e:
                last_err = f"timeout:{e}"
                logger.warning("im_translate baidu timeout %s", base)
            except Exception as e:
                last_err = str(e)
                logger.warning("im_translate baidu request failed %s: %s", base, e)
    logger.warning("im_translate baidu all endpoints failed: %s", last_err)
    return None


def translate_result(
    text: str, to_lang: str = DEFAULT_BAIDU_TO, from_lang: str = "auto"
) -> Tuple[Optional[str], Optional[str]]:
    """优先 Google，失败回退百度。返回 (译文, provider)，provider 为 google / baidu / None。"""
    q = (text or "").strip()
    if not q:
        return q, None
    to_lang = (to_lang or DEFAULT_BAIDU_TO).strip() or DEFAULT_BAIDU_TO
    cached = _cache_get(to_lang, q)
    if cached is not None:
        dst, provider = cached
        return dst, provider
    dst = _google_translate(q, to_lang, from_lang)
    google_dst = (str(dst).strip() if dst is not None else "")
    # Google 失败、空结果、或不支持/未改写原文 → 换百度
    if google_dst and google_dst != q:
        _cache_put(to_lang, q, google_dst, "google")
        return google_dst, "google"
    dst = _baidu_translate(q, to_lang, from_lang)
    baidu_dst = (str(dst).strip() if dst else "")
    if baidu_dst:
        _cache_put(to_lang, q, baidu_dst, "baidu")
        return baidu_dst, "baidu"
    if google_dst:
        _cache_put(to_lang, q, google_dst, "google")
        return google_dst, "google"
    return None, None


def translate(text: str, to_lang: str = DEFAULT_BAIDU_TO, from_lang: str = "auto") -> Optional[str]:
    """优先 Google Cloud Translation，失败回退百度。成功返回译文；都失败返回 None。"""
    dst, _provider = translate_result(text, to_lang, from_lang)
    return dst


def fill_translated_content(
    messages: List[dict],
    to_lang: str = DEFAULT_BAIDU_TO,
) -> None:
    """原地填 translated_content。未开翻译 / 已是目标语 / 失败保持 None，不改 content。"""
    for item in messages:
        item.setdefault("translated_content", None)
        text = item.get("content") or ""
        role = item.get("role") or ""
        if role not in ("user", "assistant", "operator"):
            continue
        if not needs_translation(text, to_lang):
            continue
        try:
            zh = translate(text, to_lang=to_lang)
        except Exception:
            logger.exception("im_translate one failed id=%s", item.get("id"))
            continue
        zh = (zh or "").strip()
        if zh and zh != text.strip():
            item["translated_content"] = zh


def translate_one(text: str, to_lang: str) -> dict:
    """点击翻译：返回 original / translated / target_lang / skipped / provider。"""
    original = (text or "").strip()
    target = (to_lang or DEFAULT_BAIDU_TO).strip() or DEFAULT_BAIDU_TO
    if not original:
        return {
            "original": original,
            "translated": None,
            "target_lang": target,
            "skipped": True,
            "provider": None,
        }
    if not needs_translation(original, target):
        return {
            "original": original,
            "translated": original,
            "target_lang": target,
            "skipped": True,
            "provider": None,
        }
    dst, provider = translate_result(original, to_lang=target)
    dst = (dst or "").strip() or None
    if dst == original:
        dst = original
    if not dst:
        provider = None
    return {
        "original": original,
        "translated": dst,
        "target_lang": target,
        "skipped": False,
        "provider": provider,
    }
