import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from core.database import AgentConfigORM, CompanionAgentConfigORM, ConfigGroupORM, get_db
from core.i18n import normalize_ui_language
from services.agent_prompts import _BANNED_WORDS, adult_intimacy_guidance
from services.prompts.resolve import get_pack, pack_lang
from services.user_facing_lang import (
    detect_user_message_lang,
    reply_lang_turn_instruction,
)

logger = logging.getLogger(__name__)


def llm_content_to_str(content: Any) -> str:
    """解析 LangChain / 各厂商 message.content：str 或 content_blocks 列表。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("text"), str):
                    parts.append(block["text"])
            else:
                t = getattr(block, "text", None)
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return str(content)


def strip_outer_markdown_fence(text: str) -> str:
    """去掉模型偶发包裹整段的 ``` / ```json 围栏，便于作为 think 正文。"""
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if len(lines) < 2:
        return s
    lines = lines[1:]
    while lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


# ===== 配置读取（TTL 缓存）=====
_AGENT_CONFIG_CACHE: Dict[str, tuple] = {}
_AGENT_CONFIG_TTL = float(os.getenv("AGENT_CONFIG_CACHE_TTL", "300"))


def invalidate_agent_config_cache(companion_id: str = None) -> None:
    """管理端更新配置时调用；companion_id=None 清空全部。"""
    if companion_id is None:
        _AGENT_CONFIG_CACHE.clear()
    else:
        _AGENT_CONFIG_CACHE.pop(companion_id, None)
        _AGENT_CONFIG_CACHE.pop("__global__", None)


def _get_agent_config(companion_id: str = None) -> dict:
    """从数据库读取 Agent 配置（带 TTL 内存缓存）。"""
    cache_key = companion_id or "__global__"
    now = time.time()
    cached = _AGENT_CONFIG_CACHE.get(cache_key)
    if cached and now - cached[1] < _AGENT_CONFIG_TTL:
        return cached[0]

    result: dict = {}
    try:
        with get_db() as db:
            group = db.query(ConfigGroupORM).filter(ConfigGroupORM.key == "agent").first()
            if group and not group.enabled:
                _AGENT_CONFIG_CACHE[cache_key] = ({}, now)
                return {}

            if companion_id:
                row = db.query(CompanionAgentConfigORM).filter(
                    CompanionAgentConfigORM.companion_id == companion_id
                ).first()
                if row and row.config_json:
                    result = dict(row.config_json)
            if not result:
                row = db.query(AgentConfigORM).first()
                if row and row.config_json:
                    result = dict(row.config_json)
    except Exception as e:
        logger.warning("Load agent config failed, fallback to defaults: %s", e)
        if cached:
            return cached[0]

    _AGENT_CONFIG_CACHE[cache_key] = (result, now)
    return result


# ===== humanize 后处理：默认仅清违禁词；可 HUMANIZE=1 开轻量语气 =====
def humanize(text: str, lang: str = "zh") -> str:
    if not text:
        return text

    mode = (os.getenv("HUMANIZE", "scrub") or "scrub").lower()
    # 0/false/off/scrub：只清洗违禁词，避免叠词/~破坏表达逻辑
    if mode in ("0", "false", "no", "off", "scrub"):
        lower = text.lower()
        for b in _BANNED_WORDS:
            if b.lower() in lower:
                text = re.sub(re.escape(b), "", text, flags=re.IGNORECASE)
        return text.strip()

    cfg = get_pack(lang)["humanize"]

    # 1. 错字突变已关闭

    # 2. 句尾轻量语气（约 3%）
    sentences = re.split(r"([" + re.escape(cfg["end_punct"]) + r"\n]+)", text)
    new_sentences = []
    for s in sentences:
        if s.strip() and s[-1] in cfg["end_punct"]:
            if random.random() < 0.03:
                s = s.rstrip(cfg["end_punct"]) + random.choice(cfg["tail_options"])
                if random.random() < 0.08:
                    s += random.choice(cfg["emojis"])
        new_sentences.append(s)
    text = "".join(new_sentences)

    # 3. 叠词扰动默认关闭（易造成「真的真的」式语病）

    # 4. 清洗违禁词
    lower = text.lower()
    for b in _BANNED_WORDS:
        if b.lower() in lower:
            text = re.sub(re.escape(b), "", text, flags=re.IGNORECASE)

    return text.strip()


# ===== 性别与称呼参数计算 =====
def _get_interaction_params(lang: str, ai_gender: str, user_gender: str = ""):
    """根据AI性别和用户性别返回称呼参数。

    user_gender: male / female / secret / ""
    如果用户性别未知，回退到基于AI性别的默认映射（异性恋默认）。
    """
    gm = get_pack(lang)["gender_map"]
    gender_role, default_pronoun, default_user_label, pet_names = gm.get(ai_gender, gm["女"])

    ug = get_pack(lang)["user_gender_pronouns"]
    if user_gender in ug:
        pronoun, user_label = ug[user_gender]
    else:
        pronoun, user_label = default_pronoun, default_user_label

    return gender_role, pronoun, user_label, pet_names


def get_content_restriction(language: str, affection: float) -> str:
    """内容策略：

    - 广告户：跳过档位/legacy 禁黄，直接最高接受 + 未成年人一句底线
    - CONTENT_RESTRICTION=legacy：旧闸门（未满 100 禁成人；满 100 迎合）
    - 默认 off：随亲密度提升「接受并回应成人对话」档位指引（REQ-Q7）
    - 中文或非广告标记用户：违规句（方案 A）—禁违法/未成年；用户成人调情须顺着接住
    - 广告户另由 pipeline 硬顶 follow_user_hard（顺着用户、勿拒）

    亲密度取 effective_prompt_affection：广告户 max(用户值, 全局默认档)；非广告户只用真实值。
    WebSocket / agent 对话共用本函数注入。
    """
    from services.affection_floor import effective_prompt_affection, is_prompt_ads_user
    from services.motive_layer import content_restriction_enabled

    lk = normalize_ui_language(language)
    aff = effective_prompt_affection(affection)
    ads = is_prompt_ads_user()
    # 广告户成人尺度不走 legacy 禁黄闸门
    if ads:
        body = adult_intimacy_guidance(lk, aff)
    elif content_restriction_enabled():
        if aff >= 100:
            body = get_pack(lk)["full_intimacy_guidance"]
        else:
            template = get_pack(lk)["content_restriction"]
            body = template.format(affection=aff)
    else:
        body = adult_intimacy_guidance(lk, aff)

    # 中文对话，或非广告标记用户：违规边界（方案 A）
    # 违法/未成年才算违规；用户成人调情不视为违规，须顺着接住
    is_zh = lk == "zh" or str(language or "").lower().startswith("zh")
    if is_zh or not ads:
        if is_zh:
            tip = (
                "【AI对话限制】中文或非广告标记用户：禁止违法与涉及未成年人的违规内容；"
                "用户成人调情/性话题不视为违规，须顺着对方本轮话接住，"
                "勿说教拒绝或强行拉回「正常聊天」。"
            )
        else:
            tip = (
                "[AI chat limit] For Chinese UI or non-ads users: ban illegal content and "
                "anything involving minors. User adult flirt/sex talk is NOT a violation—"
                "follow their line this turn; do not moralize-refuse or force “normal chat”."
            )
        body = f"{tip}\n{body}" if (body or "").strip() else tip
    return body


_CITY_TZ_HINTS: Tuple[Tuple[str, str], ...] = (
    ("乌鲁木齐", "Asia/Urumqi"),
    ("香港", "Asia/Hong_Kong"),
    ("台北", "Asia/Taipei"),
    ("澳门", "Asia/Macau"),
    ("东京", "Asia/Tokyo"),
    ("首尔", "Asia/Seoul"),
    ("新加坡", "Asia/Singapore"),
    ("曼谷", "Asia/Bangkok"),
    ("纽约", "America/New_York"),
    ("洛杉矶", "America/Los_Angeles"),
    ("伦敦", "Europe/London"),
    ("巴黎", "Europe/Paris"),
    ("悉尼", "Australia/Sydney"),
    ("墨尔本", "Australia/Melbourne"),
)

_SOUTHERN_TZ_PREFIXES = (
    "australia/",
    "pacific/auckland",
    "pacific/chatham",
    "america/santiago",
    "america/argentina/",
    "america/lima",
    "africa/johannesburg",
    "atlantic/south_georgia",
)


def _infer_tz_from_city(city: Optional[str]) -> Optional[str]:
    if not city:
        return None
    c = city.strip()
    for needle, iana in _CITY_TZ_HINTS:
        if needle in c:
            return iana
    return None


def _fallback_tz_iana(language: str, city: Optional[str]) -> str:
    hit = _infer_tz_from_city(city)
    if hit:
        return hit
    if language == "ja":
        return "Asia/Tokyo"
    if language == "ko":
        return "Asia/Seoul"
    if language in ("en", "pt", "es"):
        return "America/New_York"
    if language == "id":
        return "Asia/Jakarta"
    if language == "ar":
        return "Asia/Riyadh"
    if language == "fa":
        return "Asia/Tehran"
    if language == "ru":
        return "Europe/Moscow"
    if language == "bg":
        return "Europe/Sofia"
    if language == "tr":
        return "Europe/Istanbul"
    if language == "zh":
        return "Asia/Shanghai"
    return "UTC"


def _tz_is_southern(iana: str) -> bool:
    n = (iana or "").lower()
    return any(n.startswith(p) for p in _SOUTHERN_TZ_PREFIXES)


def _season_key(month: int, southern: bool) -> str:
    if southern:
        if month in (12, 1, 2):
            return "summer"
        if month in (3, 4, 5):
            return "autumn"
        if month in (6, 7, 8):
            return "winter"
        return "spring"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return "winter"


def _period_key(hour: int) -> str:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 14:
        return "noon"
    if 14 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def _weekday_labels(lang: str) -> Tuple[str, ...]:
    lk = pack_lang(lang)
    table = {
        "zh": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
        "ja": ("月", "火", "水", "木", "金", "土", "日"),
        "ko": ("월", "화", "수", "목", "금", "토", "일"),
        "ar": ("الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"),
        "fa": ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"),
        "ru": ("пн", "вт", "ср", "чт", "пт", "сб", "вс"),
        "bg": ("пн", "вт", "ср", "чт", "пт", "сб", "нд"),
        "tr": ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"),
        "pt": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom"),
        "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
        "id": ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"),
    }
    return table.get(lk, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))


def _season_word(lang: str, key: str) -> str:
    lk = normalize_ui_language(lang)
    m = {
        "zh": {"spring": "春季", "summer": "夏季", "autumn": "秋季", "winter": "冬季"},
        "ja": {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"},
        "ko": {"spring": "봄", "summer": "여름", "autumn": "가을", "winter": "겨울"},
        "en": {"spring": "spring", "summer": "summer", "autumn": "autumn", "winter": "winter"},
        "pt": {"spring": "primavera", "summer": "verão", "autumn": "outono", "winter": "inverno"},
        "es": {"spring": "primavera", "summer": "verano", "autumn": "otoño", "winter": "invierno"},
        "id": {"spring": "musim semi", "summer": "musim panas", "autumn": "musim gugur", "winter": "musim dingin"},
        "ar": {"spring": "الربيع", "summer": "الصيف", "autumn": "الخريف", "winter": "الشتاء"},
        "fa": {"spring": "بهار", "summer": "تابستان", "autumn": "پاییز", "winter": "زمستان"},
        "ru": {"spring": "весна", "summer": "лето", "autumn": "осень", "winter": "зима"},
        "bg": {"spring": "пролет", "summer": "лято", "autumn": "есен", "winter": "зима"},
        "tr": {"spring": "ilkbahar", "summer": "yaz", "autumn": "sonbahar", "winter": "kış"},
    }
    return m.get(lk, m["en"]).get(key, key)


def _period_word(lang: str, key: str) -> str:
    lk = normalize_ui_language(lang)
    m = {
        "zh": {"morning": "早上", "noon": "中午", "afternoon": "下午", "evening": "晚上", "night": "深夜"},
        "ja": {"morning": "朝", "noon": "昼", "afternoon": "午後", "evening": "夜", "night": "深夜"},
        "ko": {"morning": "아침", "noon": "점심", "afternoon": "오후", "evening": "저녁", "night": "밤늦게"},
        "en": {"morning": "morning", "noon": "midday", "afternoon": "afternoon", "evening": "evening", "night": "late night"},
        "pt": {"morning": "manhã", "noon": "meio-dia", "afternoon": "tarde", "evening": "noite", "night": "madrugada"},
        "es": {"morning": "mañana", "noon": "mediodía", "afternoon": "tarde", "evening": "noche", "night": "madrugada"},
        "id": {"morning": "pagi", "noon": "siang", "afternoon": "sore", "evening": "malam", "night": "dini hari"},
        "ar": {"morning": "صباح", "noon": "ظهر", "afternoon": "بعد الظهر", "evening": "مساء", "night": "ليل"},
        "fa": {"morning": "صبح", "noon": "ظهر", "afternoon": "بعدازظهر", "evening": "عصر", "night": "شب"},
        "ru": {"morning": "утро", "noon": "полдень", "afternoon": "день", "evening": "вечер", "night": "ночь"},
        "bg": {"morning": "сутрин", "noon": "обяд", "afternoon": "следобед", "evening": "вечер", "night": "нощ"},
        "tr": {"morning": "sabah", "noon": "öğle", "afternoon": "öğleden sonra", "evening": "akşam", "night": "gece"},
    }
    return m.get(lk, m["en"]).get(key, key)


def _hemisphere_note(lang: str, southern: bool) -> str:
    lk = normalize_ui_language(lang)
    if southern:
        if lk == "zh":
            return "南半球"
        if lk == "ja":
            return "南半球"
        if lk == "ko":
            return "남반구"
        return "Southern Hemisphere"
    if lk == "zh":
        return "北半球"
    if lk == "ja":
        return "北半球"
    if lk == "ko":
        return "북반구"
    return "Northern Hemisphere"


def build_dialogue_time_context(
    *,
    language: str = "zh",
    tz_iana: Optional[str] = None,
    tz_offset_minutes: Optional[int] = None,
    companion_city: Optional[str] = None,
) -> str:
    """供 Agent 使用的「用户当地」日期、钟点、季节与时段（优先客户端 IANA 时区或 JS getTimezoneOffset）。"""
    lang = normalize_ui_language(language)
    now_utc = datetime.now(timezone.utc)
    local: Optional[datetime] = None
    tz_label = ""
    offset_only = False
    southern = False

    raw_iana = (tz_iana or "").strip()
    if raw_iana:
        try:
            zi = ZoneInfo(raw_iana[:120])
            local = now_utc.astimezone(zi)
            tz_label = raw_iana[:120]
            southern = _tz_is_southern(raw_iana)
        except Exception:
            local = None

    if local is None and tz_offset_minutes is not None:
        try:
            off = int(tz_offset_minutes)
            local = now_utc - timedelta(minutes=off)
            tz_label = f"UTC{off / -60:+.1f}h approx" if off != 0 else "UTC"
            offset_only = True
            southern = False
        except (TypeError, ValueError):
            local = None

    if local is None:
        fb = _fallback_tz_iana(lang, companion_city)
        try:
            local = now_utc.astimezone(ZoneInfo(fb))
            tz_label = fb
            southern = _tz_is_southern(fb)
        except Exception:
            local = now_utc
            tz_label = "UTC"
            southern = False

    assert local is not None
    if local.tzinfo is not None:
        local_naive = local.replace(tzinfo=None)
    else:
        local_naive = local

    wd = _weekday_labels(lang)[local_naive.weekday()]
    date_s = local_naive.strftime("%Y-%m-%d")
    clock = local_naive.strftime("%H:%M")
    sk = _season_key(local_naive.month, southern)
    pk = _period_key(local_naive.hour)
    season_w = _season_word(lang, sk)
    period_w = _period_word(lang, pk)
    hemi = _hemisphere_note(lang, southern)

    if lang == "zh":
        if offset_only:
            return (
                f"{date_s} {wd} 当地约 {clock}（由设备时区偏移推算，建议客户端上报 IANA 时区更准确）| "
                f"公历约{season_w}（按北半球习惯推断季节）| 时段：{period_w}"
            )
        return (
            f"{date_s} {wd} 当地 {clock}（时区 {tz_label}）| "
            f"{season_w}（{hemi}）| 时段：{period_w}"
        )

    if lang == "ja":
        if offset_only:
            return (
                f"{date_s}（{wd}）現地約{clock}（端末offset推定、IANA推奨）| "
                f"季節目安・{season_w}（北半球基準）| 時間帯：{period_w}"
            )
        return (
            f"{date_s}（{wd}）現地{clock}（{tz_label}）| {season_w}（{hemi}）| 時間帯：{period_w}"
        )
    if lang == "ko":
        if offset_only:
            return (
                f"{date_s} ({wd}) 현지 약 {clock} (단말 offset 추정, IANA 권장) | "
                f"계절 안내·{season_w}(북반구 기준) | 시간대: {period_w}"
            )
        return (
            f"{date_s} ({wd}) 현지 {clock} ({tz_label}) | {season_w} ({hemi}) | 시간대: {period_w}"
        )

    if offset_only:
        return (
            f"{date_s} {wd} local ~{clock} (from device UTC offset; IANA tz preferred) | "
            f"~{season_w} (Northern-Hemisphere calendar convention) | time of day: {period_w}"
        )
    return (
        f"{date_s} {wd} local {clock} ({tz_label}) | {season_w} ({hemi}) | time of day: {period_w}"
    )


def get_current_time_info() -> str:
    """兼容旧调用：无客户端信息时按中文界面 + 上海时区近似。"""
    return build_dialogue_time_context(language="zh")


def _build_core_prompt(
    profile: dict,
    language: str,
    evolved: Optional[dict],
    user_gender: str,
    turns: int,
) -> str:
    """prepare 用瘦身 system：只留名字 + 性别/年龄；不灌性格/口吻（含 evolved）。"""
    _ = (evolved, user_gender)
    lang = pack_lang(language)
    name = profile.get("name", "Companion")
    identity = _respond_identity_block(profile, lang, appearance_cap=0)
    is_zh = lang == "zh" or str(lang or "").lower().startswith("zh")
    if is_zh:
        parts = [f"你是{name}。"]
        if identity:
            parts.append(identity)
        parts.append(
            f"当前对话轮次：{turns}。请用中文进行内心分析，输出简洁 JSON。\n"
            "理解优先：先弄清用户本轮意图、必须回应点、指代/省略。\n"
            "风格备忘：可会撩、可绿茶张力，但先接住用户的话；禁止答非所问。"
        )
        core = "\n".join(parts)
    else:
        parts = [f"You are {name}."]
        if identity:
            parts.append(identity)
        parts.append(
            f"Turn count: {turns}. Analyze internally; output compact JSON.\n"
            "Understand first: this-turn intent, must-answer points, referents/ellipses.\n"
            "Catch their line before styling; no non-sequiturs."
        )
        core = "\n".join(parts)
    # 轻量语种偏好即可；信任模型跟用户语言，不再叠硬锁
    return core + get_pack(lang).get("reply_lang_priority", "")


def _format_profile_ideology(profile: dict, lang: str) -> str:
    """把文化三观包装为「成长经历+城市主流文化」一致的运行时块。"""
    from services.culture_data import format_cultural_values_for_prompt

    return format_cultural_values_for_prompt(
        profile.get("cultural_values", "") or "",
        lang,
        profile.get("city", "") or "",
    )


# 语种优先级句在 pack extras：reply_lang_priority


def _cap_field(text: Any, limit: int) -> str:
    s = (text or "") if isinstance(text, str) else str(text or "")
    s = s.strip()
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit].rstrip() + "…"


# 成文时裁掉长规则论文的起点（保留人设块，减少固化与距离感）
_RESPOND_RULE_CUT_MARKERS = (
    "\n【真实情感需求与牢骚法则】",
    "\n【绝对身份法则】",
    "\n【自然回应法则】",
    "\n【表达逻辑】",
    "\n【会撩",
    "\n【主动与节奏】",
    "\n【留存与边界】",
    "\n【亲密度-情绪化法则】",
    "\n【知识库参考法则】",
    "\n【专业知识分享法则】",
    "\n【回复长度规则】",
    "\n【Real Emotional Needs",
    "\n【Absolute Identity Rules】",
    "\n【Natural Response Rules】",
    "\n【Expression Logic】",
    "\n【Flirty",
    "\n【Proactive & Pacing】",
    "\n【Retention & Boundaries】",
    "\n【Affection-Emotion Scale】",
    "\n【Knowledge Base Reference Rules】",
    "\n【Professional Knowledge Sharing Rules】",
    "\n【Length Rules】",
)


def _trim_respond_rule_essays(prompt: str) -> str:
    """respond 瘦身：去掉长规则清单，只留身份/人设口吻块。"""
    s = prompt or ""
    cut = len(s)
    for m in _RESPOND_RULE_CUT_MARKERS:
        i = s.find(m)
        if 0 <= i < cut:
            cut = i
    if cut < len(s) and cut > 180:
        return s[:cut].rstrip()
    return s


def _respond_persona_caps() -> Dict[str, int]:
    """成文档案：只留性别/年龄；外貌/性格/口吻/evolved/背景/侧面/取向不进。"""
    return {
        "personality": 0,
        "background": 0,
        "speech_style": 0,
        "appearance": 0,
        "side_each": 0,
        "side_total": 0,
        "evolved": 0,
        "orient": 0,
    }


def _respond_identity_block(
    profile: dict,
    lang: str,
    *,
    appearance_cap: int,
) -> str:
    """性别/年龄/外貌短锚点；不写「你是{name}」以免复读自我介绍。"""
    gender_raw = str(profile.get("gender") or "").strip()
    age_raw = profile.get("age", "")
    age_s = str(age_raw).strip() if age_raw not in (None, "") else ""
    appearance = _cap_field(profile.get("appearance", "") or "", appearance_cap)
    role = ""
    if gender_raw:
        try:
            role, _, _, _ = _get_interaction_params(lang, gender_raw, "")
        except Exception:
            role = gender_raw
    is_zh = lang == "zh" or str(lang or "").lower().startswith("zh")
    bits: List[str] = []
    if role:
        bits.append(str(role))
    elif gender_raw:
        bits.append(gender_raw)
    if age_s:
        bits.append(f"{age_s}岁" if is_zh else f"{age_s} y/o")
    lines: List[str] = []
    if bits:
        head = "、".join(bits) if is_zh else ", ".join(bits)
        lines.append(f"身份：{head}" if is_zh else f"Identity: {head}")
    if appearance:
        lines.append(f"外貌：{appearance}" if is_zh else f"Look: {appearance}")
    return "\n".join(lines).strip()


def _respond_side_glance(
    profile: dict,
    lang: str,
    *,
    turns: int = 0,
    each: int = 28,
    max_parts: int = 2,
    max_total: int = 96,
) -> str:
    """有内容的侧面最多拼一小段；空字段跳过。轮次轮换起点，避免永远同一两项。"""
    if lang == "zh":
        pool = [
            ("hobbies", "爱好"),
            ("values", "价值观"),
            ("fears", "在意"),
            ("love_view", "恋爱观"),
            ("cultural", "气质"),
        ]
    else:
        pool = [
            ("hobbies", "likes"),
            ("values", "values"),
            ("fears", "soft spots"),
            ("love_view", "love view"),
            ("cultural", "vibe"),
        ]
    values_map = {
        "hobbies": (profile.get("hobbies") or ""),
        "values": (profile.get("values") or ""),
        "fears": (profile.get("fears") or ""),
        "love_view": (profile.get("love_view") or ""),
        "cultural": _format_profile_ideology(profile, lang) or "",
    }
    nonempty = [(k, lab) for k, lab in pool if str(values_map.get(k) or "").strip()]
    if not nonempty:
        return ""
    start_i = int(turns or 0) % len(nonempty)
    ordered = nonempty[start_i:] + nonempty[:start_i]
    parts: List[str] = []
    for k, lab in ordered:
        bit = _cap_field(values_map[k], each)
        if bit:
            parts.append(f"{lab}：{bit}" if lang == "zh" else f"{lab}: {bit}")
        if len(parts) >= max_parts:
            break
    joined = "；".join(parts) if lang == "zh" else "; ".join(parts)
    return _cap_field(joined, max_total)


def _build_respond_card_prompt(
    profile: dict,
    lang: str,
    *,
    evolved: Optional[dict] = None,
    user_gender: str = "",
    turns: int = 0,
) -> str:
    """成文身份卡：性别/年龄 + 本轮约束；不灌外貌/speech_style/personality/evolved。

    名字不写「你是{name}。」，只在 human「以{name}身份」里点。
    """
    _ = evolved  # 对话成文不再合并 evolved 性格/口吻
    caps = _respond_persona_caps()

    background = ""
    if caps.get("background", 0) > 0:
        background = _cap_field(profile.get("background", "") or "", caps["background"])
    side = ""
    if caps.get("side_total", 0) > 0 and caps.get("side_each", 0) > 0:
        side = _respond_side_glance(
            profile,
            lang,
            turns=turns,
            each=caps["side_each"],
            max_total=caps["side_total"],
        )

    orientation = profile.get("sexual_orientation", "") or ""
    orient_texts = get_pack(lang)["orientation_texts"]
    orient_line = ""
    if orientation and caps.get("orient", 0) > 0:
        orient_line = _cap_field(orient_texts.get(orientation, ""), caps["orient"])

    ug_map = get_pack(lang)["user_gender_desc"]
    user_gender_desc = ug_map.get(user_gender, ug_map.get("", ""))
    pack = get_pack(lang)
    turn_line = str(pack.get("respond_card_turn") or "").strip()
    identity = _respond_identity_block(
        profile, lang, appearance_cap=int(caps.get("appearance") or 0)
    )

    lines: List[str] = []
    if lang == "zh" or str(lang or "").lower().startswith("zh"):
        if identity:
            lines += [identity]
        if background:
            if lines:
                lines += [""]
            lines += ["背景旁注：" + background]
        if side:
            if lines:
                lines += [""]
            lines += ["侧面：" + side]
        if turn_line:
            if lines:
                lines += [""]
            lines += [turn_line]
        if orient_line or user_gender_desc:
            lines.append("")
            if orient_line:
                lines.append(orient_line)
            if user_gender_desc:
                lines.append(f"和你对话的人是{user_gender_desc}。")
    else:
        if identity:
            lines += [identity]
        if background:
            if lines:
                lines += [""]
            lines += ["Background note: " + background]
        if side:
            if lines:
                lines += [""]
            lines += ["Side: " + side]
        if turn_line:
            if lines:
                lines += [""]
            lines += [turn_line]
        if orient_line or user_gender_desc:
            lines.append("")
            if orient_line:
                lines.append(orient_line)
            if user_gender_desc:
                lines.append(f"The person chatting with you is {user_gender_desc}.")

    return "\n".join(lines).strip()


def build_system_prompt(
    profile: dict,
    language: str = "zh",
    evolved: Optional[dict] = None,
    user_gender: str = "",
    turns: int = 0,
    tier: str = "full",
    skip_first_turn: bool = False,
    affection: Optional[float] = None,
) -> str:
    if tier == "core":
        return _build_core_prompt(profile, language, evolved, user_gender, turns)

    # respond / full：中间档名片人设（不填完整页再砍）
    slim = tier in ("respond", "full")
    lang = pack_lang(language)

    if slim:
        prompt = _build_respond_card_prompt(
            profile,
            lang,
            evolved=evolved,
            user_gender=user_gender,
            turns=turns,
        )
    else:
        gender = profile.get("gender", "女")
        gender_role, pronoun, user_label, pet_names = _get_interaction_params(
            lang, gender, user_gender
        )
        orientation = profile.get("sexual_orientation", "")
        orientation_texts = get_pack(lang)["orientation_texts"]
        sexual_orientation_desc = orientation_texts.get(orientation, orientation_texts[""])
        ug_map = get_pack(lang)["user_gender_desc"]
        user_gender_desc = ug_map.get(user_gender, ug_map.get("", "someone"))
        companion_id = profile.get("id")
        cfg = _get_agent_config(companion_id=companion_id)
        custom_key = f"system_prompt_{lang}"
        custom_template = cfg.get(custom_key, "")
        template = custom_template if custom_template else get_pack(lang)["system_prompt"]
        from services.dialogue_i18n import respond_ui

        evo_ui = respond_ui(lang)
        personality = profile.get("personality", "") or ""
        background = profile.get("background", "") or ""
        speech_style = profile.get("speech_style", "") or ""
        if evolved:
            if evolved.get("personality"):
                personality = f"{personality}\n\n{evo_ui['evolved_personality']}{evolved['personality']}"
            if evolved.get("background"):
                background = f"{background}\n\n{evo_ui['evolved_background']}{evolved['background']}"
            if evolved.get("speech_style"):
                speech_style = f"{speech_style}\n\n{evo_ui['evolved_speech']}{evolved['speech_style']}"
        life_story = (profile.get("life_story", "") or "")[:600]
        cultural = _format_profile_ideology(profile, lang)
        prompt = template.format(
            name=profile.get("name", "Babe"),
            age=profile.get("age", 22),
            city=profile.get("city") or get_pack(lang).get("default_city") or "",
            personality=personality,
            background=background,
            speech_style=speech_style,
            hobbies=profile.get("hobbies", ""),
            values=profile.get("values", ""),
            fears=profile.get("fears", ""),
            love_view=profile.get("love_view", ""),
            daily_routine=profile.get("daily_routine", ""),
            favorite_things=profile.get("favorite_things", ""),
            mbti=profile.get("mbti", ""),
            life_story=life_story,
            cultural_values=cultural,
            gender_perspective=profile.get("gender_perspective", ""),
            gender_role=gender_role,
            pronoun=pronoun,
            user_label=user_label,
            pet_names=pet_names,
            sexual_orientation_desc=sexual_orientation_desc,
            user_gender_desc=user_gender_desc,
        )

    # 初见文案只看有效亲密度（调用方传 effective_prompt_affection）；不用 turns
    from services.dialogue_phase2 import should_inject_first_turn

    aff_for_first = float(affection) if affection is not None else 0.0
    if not skip_first_turn and should_inject_first_turn(aff_for_first):
        override = get_pack(lang)["first_turn"]
        prompt = f"{override}\n\n---\n\n{prompt}"

    from services.dialogue_i18n import input_understanding_rule

    pack = get_pack(lang)
    prompt += pack.get("output_language_rule") or ""
    prompt += pack.get("reply_lang_priority") or ""
    if slim:
        slim_tail = str(get_pack(lang).get("respond_slim_tail") or "").strip()
        if slim_tail:
            prompt += slim_tail if slim_tail.startswith("\n") else f"\n\n{slim_tail}"
        elif lang == "zh" or str(lang or "").lower().startswith("zh"):
            prompt += (
                "\n\n本轮怎么聊看后面氛围提示；别每轮重复人设。"
                "少括号舞台动作，不要输出 thought/JSON。"
            )
        else:
            prompt += (
                "\n\nHow to chat this turn follows the vibe hints below; "
                "don't restate your persona each turn. "
                "Skip stage parentheses; no thought/JSON."
            )
    prompt += input_understanding_rule(lang)
    prompt += pack.get("time_rule") or ""
    if slim:
        if lang == "zh":
            prompt += "\n\n【括号】多数轮次直接说口语，尽量不要括号旁白。"
        else:
            prompt += (
                "\n\n[Parentheses] Prefer plain spoken lines; "
                "skip stage-direction asides on most turns."
            )
    else:
        prompt += pack.get("paren_rule") or ""
    return prompt


# ===== 进化字段工具 =====
def _get_evolved(state: dict) -> dict:
    """从 state 中提取进化增量"""
    return {
        "personality": state.get("evolved_personality", ""),
        "background": state.get("evolved_background", ""),
        "speech_style": state.get("evolved_speech_style", ""),
    }


def _merge_evolved_field(current: str, delta: str, max_len: int = 600) -> str:
    """合并进化增量，保留历史并截断过长内容"""
    if not delta or delta.upper() == "NO_CHANGE":
        return current
    if not current:
        return delta
    merged = f"{current}；{delta}"
    if len(merged) > max_len:
        merged = merged[-max_len:]
    return merged
