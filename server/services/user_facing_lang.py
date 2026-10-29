"""对用户可见正文语种。

默认跟用户输入脚本（防人设/UI 乱切）；明确「助手输出语」指令可覆盖。
门禁要点：
- prepare.output_lang 不得与本轮脚本冲突
- sticky==UI 且与本轮/近史脚本冲突 → 视为 UI 泄漏并清除
- 故意 sticky（如「我说中文你说英文」的 en）优先于 prepare
人设 language / 城市主流语不参与判定。

识别：假名优先于汉字；拉丁/西里尔轻量消歧（禁止非本语 UI 一律 en/ru）。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from services.prompts.resolve import SUPPORTED_LANGS, get_pack, pack_lang

# session 字段名
REPLY_LANG_STICKY_KEY = "reply_lang_override"

_LATIN_LANGS = ("en", "es", "pt", "id", "tr")
_CYR_LANGS = ("ru", "bg")


def _text_has_script(user_input: str) -> bool:
    """是否含可检测文字脚本（忽略纯表情/空白）。"""
    t = user_input or ""
    return bool(
        re.search(r"[\u4e00-\u9fff]", t)
        or re.search(r"[\u3040-\u30ff]", t)
        or re.search(r"[\uac00-\ud7af]", t)
        or re.search(r"[\u0600-\u06FF]", t)
        or re.search(r"[\u0400-\u04FF]", t)
        or re.search(r"[A-Za-z]", t)
    )


def _score_hits(text: str, patterns: Tuple[str, ...]) -> int:
    t = (text or "").lower()
    return sum(1 for p in patterns if p.lower() in t)


def _disambiguate_latin(text: str, fallback: str) -> str:
    """拉丁五语消歧：特征打分；禁止非拉丁 UI 时一律 en。"""
    fb = pack_lang(fallback)
    raw = text or ""
    low = raw.lower()

    scores = {k: 0 for k in _LATIN_LANGS}
    if re.search(r"[¿¡ñ]", raw, re.I):
        scores["es"] += 2
    scores["es"] += _score_hits(
        low,
        (
            "qué",
            "que ",
            "cómo",
            "como ",
            "está",
            "estas",
            "estás",
            "hola",
            "llamo",
            "también",
            "tambien",
            "gracias",
            "buenos",
            "buenas",
            "por favor",
            "tengo",
            "calor",
            "español",
            "espanol",
            "habla",
            "amigo",
            "usted",
            "ustedes",
            "mañana",
            "manana",
        ),
    )
    if re.search(r"[ãõç]|ção|ções", raw, re.I):
        scores["pt"] += 2
    scores["pt"] += _score_hits(
        low,
        (
            "você",
            "voce",
            "vocês",
            "nao ",
            "não",
            "obrigad",
            "tudo bem",
            "portugu",
            "amanhã",
            "amanha",
            "estou",
            "desculpa",
            "pode ",
            "fala ",
            "fale ",
        ),
    )
    scores["id"] += _score_hits(
        low,
        (
            "aku ",
            "kamu",
            "tidak",
            "apa kabar",
            "sudah",
            "dengan",
            "yang ",
            "bisa ",
            "terima kasih",
            "bahasa indonesia",
            "ngapain",
            "halo ",
            "selamat",
        ),
    )
    if re.search(r"[ıİğüşöçŞĞÜÖÇ]", raw):
        scores["tr"] += 2
    scores["tr"] += _score_hits(
        low,
        (
            "merhaba",
            "nasıl",
            "nasil",
            "teşekkür",
            "tesekkur",
            "lütfen",
            "lutfen",
            "türkçe",
            "turkce",
            "degil",
            "değil",
            "icin",
            "için",
            "bugun",
            "bugün",
            "gunaydin",
            "günaydın",
        ),
    )
    scores["en"] += _score_hits(
        low,
        (
            " the ",
            " you ",
            " are ",
            " what ",
            " don't",
            "please",
            "thanks",
            "hello",
            "hey ",
            "how are",
            "today",
            "english",
        ),
    )
    if re.search(r"\b(the|you|what|don't|please|thanks|hello|hey)\b", low):
        scores["en"] += 1

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    best_lang, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0

    if best >= 2 and best > second:
        return best_lang
    if fb not in _LATIN_LANGS and best >= 1 and best > second:
        return best_lang
    if fb not in _LATIN_LANGS and best >= 2:
        return best_lang
    if fb in _LATIN_LANGS:
        return fb
    if best >= 1:
        return best_lang
    return "en"


def _disambiguate_cyrillic(text: str, fallback: str) -> str:
    """西里尔：ru/bg 消歧。"""
    fb = pack_lang(fallback)
    low = (text or "").lower()
    scores = {"ru": 0, "bg": 0}
    scores["bg"] += _score_hits(
        low,
        (
            "здравей",
            "здрасти",
            "къде",
            "какво",
            "съм",
            "това",
            "моля",
            "благодаря",
            "българ",
            "още",
            "защо",
        ),
    )
    if "ъ" in low or "щ" in low:
        scores["bg"] += 1
    scores["ru"] += _score_hits(
        low,
        (
            "привет",
            "что",
            "меня",
            "это",
            "спасибо",
            "пожалуйста",
            "почему",
            "сегодня",
            "русск",
            "хорошо",
            "тебя",
        ),
    )
    if scores["bg"] > scores["ru"]:
        return "bg"
    if scores["ru"] > scores["bg"]:
        return "ru"
    if fb in _CYR_LANGS:
        return fb
    return "ru"


def detect_user_message_lang(user_input: str, fallback: str = "en") -> str:
    """按用户本轮文本脚本推断语种（输入侧；不等于不可覆盖的回复语）。"""
    t = user_input or ""
    fb = pack_lang(fallback)
    # 假名优先于汉字：日常日语「汉字+假名」不得判成 zh
    if re.search(r"[\u3040-\u30ff]", t):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", t):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", t):
        return "zh"
    if re.search(r"[پچژگکی]", t):
        return "fa"
    if re.search(r"[\u0600-\u06FF]", t):
        # 波斯常用词（无专用字母时也不要一律判 ar）
        if re.search(
            r"(سلام|عشقم|عزیزم|خوبی|خوبه|چطور|چطوری|میخوام|میخوای|"
            r"میکنی|فارسی|ممنون|مرسی|خسته|باش|کنیم|هستم)",
            t,
        ):
            return "fa"
        if fb in ("ar", "fa"):
            return fb
        return "ar"
    if re.search(r"[\u0400-\u04FF]", t):
        return _disambiguate_cyrillic(t, fb)
    if re.search(r"[A-Za-z]", t):
        return _disambiguate_latin(t, fb)
    return fb if fb in SUPPORTED_LANGS else "en"


def latest_user_script_lang(
    history_user_texts: Optional[List[str]] = None,
    fallback: str = "en",
) -> Optional[str]:
    """近史里最近一条有脚本的用户句语种；没有则 None。"""
    fb = pack_lang(fallback)
    hist = [str(x) for x in (history_user_texts or []) if str(x or "").strip()]
    for t in reversed(hist):
        if _text_has_script(t):
            return detect_user_message_lang(t, fb)
    return None


def normalize_output_lang(raw: Optional[str]) -> Optional[str]:
    """校验为已有 pack 短码；非法则 None。"""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s in ("null", "none", "same", "auto", "default", ""):
        return None
    aliases = {
        "chinese": "zh",
        "mandarin": "zh",
        "english": "en",
        "japanese": "ja",
        "korean": "ko",
        "portuguese": "pt",
        "spanish": "es",
        "indonesian": "id",
        "arabic": "ar",
        "persian": "fa",
        "farsi": "fa",
        "russian": "ru",
        "bulgarian": "bg",
        "turkish": "tr",
        "中文": "zh",
        "汉语": "zh",
        "英文": "en",
        "英语": "en",
        "日文": "ja",
        "日语": "ja",
        "韩文": "ko",
        "韩语": "ko",
        "葡萄牙语": "pt",
        "西班牙语": "es",
        "印尼语": "id",
        "阿拉伯语": "ar",
        "波斯语": "fa",
        "俄语": "ru",
        "保加利亚语": "bg",
        "土耳其语": "tr",
    }
    if s in aliases:
        return aliases[s]
    key = pack_lang(s)
    return key if key in SUPPORTED_LANGS else None


def resolve_user_facing_lang(
    current_text: Optional[str] = None,
    history_user_texts: Optional[List[str]] = None,
    ui_lang: Optional[str] = None,
) -> str:
    """默认跟随：本轮脚本 → 历史用户消息 → UI（无人设语）。"""
    ui = pack_lang(ui_lang or "en")
    cur = (current_text or "").strip()
    if cur and _text_has_script(cur):
        return detect_user_message_lang(cur, ui)

    votes: List[str] = []
    hist = [str(x) for x in (history_user_texts or []) if str(x or "").strip()]
    for t in reversed(hist[-12:]):
        if not _text_has_script(t):
            continue
        votes.append(detect_user_message_lang(t, ui))
    if votes:
        counts = Counter(votes)
        top = max(counts.values())
        for lang in votes:
            if counts[lang] == top:
                return pack_lang(lang)
    return ui


_LANG_COMMAND_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("en", re.compile(
        r"(说英文|用英文|讲英语|改英文|切换到英文|用英语回|英文回|"
        r"speak\s*english|in\s*english|reply\s*in\s*english|switch\s*to\s*english|"
        r"英語で|英語を話|영어로|speak\s*in\s*english|"
        r"ingilizce\s*konu[sş]|hablar?\s+ingl[eé]s|fala(?:r)?\s+ingl[eê]s)",
        re.I,
    )),
    ("zh", re.compile(
        r"(说中文|用中文|讲中文|改中文|切换到中文|用中文回|中文回|"
        r"speak\s*chinese|in\s*chinese|reply\s*in\s*chinese|用汉语)",
        re.I,
    )),
    ("ja", re.compile(
        r"(说日语|用日语|讲日文|日本語で|speak\s*japanese|in\s*japanese)",
        re.I,
    )),
    ("ko", re.compile(
        r"(说韩语|用韩语|한국어로|speak\s*korean|in\s*korean)",
        re.I,
    )),
    ("pt", re.compile(
        r"(说葡语|用葡萄牙语|em\s*portugu[eê]s|speak\s*portuguese|"
        r"fala(?:r)?(?:\s+em)?\s+portugu[eê]s|fale(?:\s+em)?\s+portugu[eê]s|"
        r"responde(?:r)?\s+em\s+portugu[eê]s)",
        re.I,
    )),
    ("es", re.compile(
        r"(说西语|用西班牙语|en\s*espa[nñ]ol|speak\s*spanish|"
        r"habla(?:r)?(?:\s+en)?\s+espa[nñ]ol|responde(?:r)?\s+en\s+espa[nñ]ol|"
        r"espa[nñ]ol\s+por\s+favor)",
        re.I,
    )),
    ("id", re.compile(
        r"(说印尼语|bahasa\s*indonesia|speak\s*indonesian|"
        r"bicara(?:lah)?\s+bahasa\s+indonesia|jawab\s+dalam\s+bahasa\s+indonesia|"
        r"pakai\s+bahasa\s+indonesia)",
        re.I,
    )),
    ("ar", re.compile(
        r"(说阿拉伯语|بالعربية|speak\s*arabic|تحدث(?:ي)?\s*بالعربية|رد\s*بالعربية)",
        re.I,
    )),
    ("fa", re.compile(
        r"(说波斯语|به\s*فارسی|speak\s*persian|speak\s*farsi|فارسی\s*صحبت)",
        re.I,
    )),
    ("ru", re.compile(
        r"(说俄语|по-русски|speak\s*russian|говори(?:те)?\s*по-русски|"
        r"отвечай(?:те)?\s*по-русски)",
        re.I,
    )),
    ("bg", re.compile(
        r"(说保加利亚语|на\s*български|speak\s*bulgarian|"
        r"говори(?:те)?\s*на\s*български|отговори(?:те)?\s*на\s*български)",
        re.I,
    )),
    ("tr", re.compile(
        r"(说土耳其语|türkçe|turkce|speak\s*turkish|"
        r"türkçe\s*konu[sş]|turkce\s*konus|türkçe\s*yaz|turkce\s*yaz)",
        re.I,
    )),
)

_ASYMMETRIC = re.compile(
    r"(?:我(?:说|用|讲)\s*)(中文|汉语|英文|英语|日语|日文|韩语|韩文|english|chinese|japanese|korean)"
    r".{0,12}"
    r"(?:你(?:说|用|讲)\s*)(中文|汉语|英文|英语|日语|日文|韩语|韩文|english|chinese|japanese|korean)",
    re.I,
)

_CLEAR_FOLLOW_USER = re.compile(
    r"(跟我一样说|正常说话|恢复默认|别固定语[言种]了|follow\s*my\s*language|"
    r"speak\s*like\s*me|match\s*my\s*language|no\s*more\s*forced\s*language)",
    re.I,
)


def detect_output_lang_command(text: str) -> Optional[str]:
    """快路径解析「助手输出语」；无命中返回 None（交给 prepare / 默认跟随）。"""
    t = (text or "").strip()
    if not t:
        return None
    m = _ASYMMETRIC.search(t)
    if m:
        return normalize_output_lang(m.group(2))
    m2 = re.search(
        r"你(?:说|用|讲)\s*(中文|汉语|英文|英语|日语|日文|韩语|韩文|english|chinese|japanese|korean)",
        t,
        re.I,
    )
    if m2:
        return normalize_output_lang(m2.group(1))
    for lang, pat in _LANG_COMMAND_PATTERNS:
        if pat.search(t):
            return lang
    return None


def detect_clear_lang_override(text: str) -> bool:
    """用户要求取消固定输出语、改回跟我说的语言。"""
    return bool(_CLEAR_FOLLOW_USER.search(text or ""))


_PAREN_THINK = re.compile(r"[（(][^）)]*[）)]")


def reply_conflicts_expected_lang(reply: str, expected_lang: str) -> bool:
    """成文后抽检：回复主脚本是否偏离期望语（全语种）。

    抓远距文字（阿↔中、拉丁↔假名等），以及拉丁语族互切（西语场整段英文等）。
    短码切换/弱特征不判冲突（消歧置信不足时跟期望语）。
    """
    exp = pack_lang(expected_lang or "en")
    raw = _PAREN_THINK.sub("", reply or "")
    body = re.sub(r"\s+", "", raw)
    if len(body) < 8:
        return False
    n_zh = len(re.findall(r"[\u4e00-\u9fff]", body))
    n_ja = len(re.findall(r"[\u3040-\u30ff]", body))
    n_ko = len(re.findall(r"[\uac00-\ud7af]", body))
    n_ar = len(re.findall(r"[\u0600-\u06FF]", body))
    n_cyr = len(re.findall(r"[\u0400-\u04FF]", body))
    n_lat = len(re.findall(r"[A-Za-z]", body))
    n_cjk = n_zh + n_ja

    if exp == "zh":
        return (
            (n_ar >= 8 and n_ar > n_zh + 2)
            or (n_cyr >= 8 and n_cyr > n_zh + 2)
            or (n_ko >= 8 and n_ko > n_zh + 2)
            or (n_ja >= 8 and n_ja >= n_zh)  # 假名主导 → 更像日语
            or (n_lat >= 20 and n_lat > n_zh + 10)
        )
    if exp == "ja":
        own = n_ja + n_zh
        # 纯中文（几乎无假名）
        if n_zh >= 12 and n_ja <= 2 and n_zh > n_ja + 8:
            return True
        return (
            (n_ar >= 8 and n_ar > own + 2)
            or (n_ko >= 8 and n_ko > own + 2)
            or (n_cyr >= 8 and n_cyr > own + 2)
            or (n_lat >= 20 and n_lat > own + 8 and n_ja <= 2)
        )
    if exp == "ko":
        return (
            (n_ar >= 8 and n_ar > n_ko + 2)
            or (n_ja >= 8 and n_ja > n_ko + 2)
            or (n_zh >= 14 and n_zh > n_ko + 6)
            or (n_cyr >= 8 and n_cyr > n_ko + 2)
            or (n_lat >= 20 and n_lat > n_ko + 8)
        )
    if exp in ("ar", "fa"):
        return (
            (n_zh >= 8 and n_zh > n_ar + 2)
            or (n_ja >= 8 and n_ja > n_ar + 2)
            or (n_ko >= 8 and n_ko > n_ar + 2)
            or (n_cyr >= 8 and n_cyr > n_ar + 2)
            or (n_lat >= 20 and n_lat > n_ar + 8)
        )
    if exp in ("ru", "bg"):
        return (
            (n_zh >= 8 and n_zh > n_cyr + 2)
            or (n_ar >= 8 and n_ar > n_cyr + 2)
            or (n_ja >= 8 and n_ja > n_cyr + 2)
            or (n_ko >= 8 and n_ko > n_cyr + 2)
            or (n_lat >= 20 and n_lat > n_cyr + 8)
        )
    if exp in _LATIN_LANGS:
        # 远距脚本抢戏
        if (
            (n_ar >= 8 and n_ar > n_lat + 2)
            or (n_cyr >= 8 and n_cyr > n_lat + 2)
            or (n_ko >= 6 and n_ko > n_lat)
            or (n_ja >= 6 and n_ja > n_lat)
            or (n_zh >= 10 and n_zh > n_lat + 4 and n_ja <= 2)
            or (n_cjk >= 10 and n_cjk > n_lat + 4)
        ):
            return True
        # 拉丁互切：整段明显是另一拉丁语（复用消歧，弱特征回落期望语）
        other_scripts = n_zh + n_ja + n_ko + n_ar + n_cyr
        if n_lat >= 24 and other_scripts <= 2:
            guessed = _disambiguate_latin(raw, exp)
            if guessed in _LATIN_LANGS and guessed != exp:
                return True
        return False
    return False


def resolve_reply_lang(
    *,
    current_text: Optional[str] = None,
    history_user_texts: Optional[List[str]] = None,
    ui_lang: Optional[str] = None,
    sticky: Optional[str] = None,
    output_lang: Optional[str] = None,
    channel: str = "chat",
) -> Dict[str, Any]:
    """统一回复语种：换语指令 → sticky →（校验后的）prepare → 脚本/历史 → UI。"""
    ui = pack_lang(ui_lang or "en")
    channel = (channel or "chat").strip().lower()
    sticky_n = normalize_output_lang(sticky)
    prepared = normalize_output_lang(output_lang)
    cur = (current_text or "").strip()
    hist = [str(x) for x in (history_user_texts or []) if str(x or "").strip()]

    clear = bool(cur and detect_clear_lang_override(cur))
    if clear:
        sticky_n = None

    cmd = detect_output_lang_command(cur) if cur else None
    script_lang: Optional[str] = None
    if cur and _text_has_script(cur):
        script_lang = detect_user_message_lang(cur, ui)

    if script_lang and not cmd and not clear:
        if prepared and prepared != script_lang:
            prepared = None

    sticky_anchor = script_lang
    if (
        not sticky_anchor
        and channel in ("chat", "proactive")
        and any(_text_has_script(t) for t in hist)
    ):
        sticky_anchor = resolve_user_facing_lang(None, hist, ui)
    if (
        sticky_anchor
        and not cmd
        and not clear
        and channel in ("chat", "proactive")
        and sticky_n
        and sticky_n != sticky_anchor
        and sticky_n == ui
    ):
        sticky_n = None

    if cmd:
        new_sticky = cmd if channel == "chat" else sticky_n
        return {
            "reply_lang": cmd,
            "sticky": new_sticky,
            "source": "command",
            "cleared": clear,
            "script_lang": script_lang,
        }

    if channel == "moment_comment":
        if cur and _text_has_script(cur):
            lang = detect_user_message_lang(cur, ui)
            return {
                "reply_lang": lang,
                "sticky": sticky_n,
                "source": "script",
                "cleared": clear,
                "script_lang": lang,
            }
        if sticky_n:
            return {
                "reply_lang": sticky_n,
                "sticky": sticky_n,
                "source": "sticky",
                "cleared": clear,
                "script_lang": script_lang,
            }

    # 主动消息：硬跟近史最近用户脚本，禁止 UI/多数票抢戏（挡西语追聊蓝框）
    if channel == "proactive":
        if sticky_n and sticky_n == ui:
            sticky_n = None
        latest = latest_user_script_lang(hist, ui)
        if latest:
            return {
                "reply_lang": latest,
                "sticky": sticky_n,
                "source": "history_latest",
                "cleared": clear,
                "script_lang": latest,
            }

    if sticky_n:
        return {
            "reply_lang": sticky_n,
            "sticky": sticky_n,
            "source": "sticky",
            "cleared": clear,
            "script_lang": script_lang,
        }

    if prepared:
        if channel == "chat":
            new_sticky = prepared if script_lang and prepared == script_lang else sticky_n
        else:
            new_sticky = sticky_n
        return {
            "reply_lang": prepared,
            "sticky": new_sticky,
            "source": "prepare",
            "cleared": clear,
            "script_lang": script_lang,
        }

    # 无本轮文案时优先近史最近一句
    if not cur:
        latest = latest_user_script_lang(hist, ui)
        if latest:
            return {
                "reply_lang": latest,
                "sticky": sticky_n,
                "source": "history_latest",
                "cleared": clear,
                "script_lang": latest,
            }

    lang = resolve_user_facing_lang(current_text, history_user_texts, ui)
    return {
        "reply_lang": lang,
        "sticky": sticky_n,
        "source": "default",
        "cleared": clear,
        "script_lang": script_lang or lang,
    }


def recent_user_texts_from_messages(
    messages: Optional[List[Dict[str, Any]]], limit: int = 12
) -> List[str]:
    """从 short_term 消息列表抽出用户气泡（时间正序，末尾最新）。"""
    out: List[str] = []
    for m in messages or []:
        if m.get("role") == "user" and str(m.get("content") or "").strip():
            out.append(str(m["content"]))
    return out[-max(1, int(limit or 12)) :]


_LANG_DISPLAY = {
    "zh": "简体中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "pt": "português",
    "es": "español",
    "id": "bahasa Indonesia",
    "ar": "العربية",
    "fa": "فارسی",
    "ru": "русский",
    "bg": "български",
    "tr": "Türkçe",
}


def reply_lang_turn_instruction(
    user_input: str = "",
    fallback_lang: str = "en",
    history_user_texts: Optional[List[str]] = None,
    *,
    reply_lang: Optional[str] = None,
    sticky: Optional[str] = None,
    output_lang: Optional[str] = None,
    channel: str = "chat",
) -> str:
    """成文前注入：轻量语种偏好（跟用户），不做「绝对强制」。

    主成文信任模型跟聊语种；仅给一句偏好提示，避免多层硬锁与模型抢注意力。
    """
    if reply_lang:
        lk = pack_lang(reply_lang)
    else:
        resolved = resolve_reply_lang(
            current_text=user_input,
            history_user_texts=history_user_texts,
            ui_lang=fallback_lang,
            sticky=sticky,
            output_lang=output_lang,
            channel=channel,
        )
        lk = resolved["reply_lang"]

    display = _LANG_DISPLAY.get(lk, lk)
    if lk == "zh":
        return (
            f"\n\n【语种偏好】默认用{display}跟用户聊；"
            "有明确换语要求时按要求。人设字段里的其它语言只是设定，不必照抄。"
        )
    return (
        f"\n\n[Language preference] Prefer chatting in {display} with the user; "
        "follow an explicit language switch if they asked. "
        "Other languages in persona fields are lore only—no need to copy them."
    )


def apply_sticky_to_session(session_meta: dict, sticky: Optional[str]) -> dict:
    """写入/清除 session 中的回复语 sticky。"""
    meta = dict(session_meta or {})
    s = normalize_output_lang(sticky)
    if s:
        meta[REPLY_LANG_STICKY_KEY] = s
    else:
        meta.pop(REPLY_LANG_STICKY_KEY, None)
    return meta
