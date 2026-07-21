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
from services.agent_prompts import (
    _BANNED_WORDS,
    _CONTENT_RESTRICTIONS,
    _FULL_INTIMACY_GUIDANCE,
    _GENDER_MAP,
    _HUMANIZE_CONFIG,
    _SEXUAL_ORIENTATION_TEXTS,
    _SYSTEM_PROMPTS,
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


# ===== humanize 后处理：按语言让文字像真人 =====
def humanize(text: str, lang: str = "zh") -> str:
    if not text:
        return text

    cfg = _HUMANIZE_CONFIG.get(lang, _HUMANIZE_CONFIG["zh"])

    # 1. 偶尔小错别字（约 3% 概率）
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in cfg["typo_map"] and random.random() < 0.03:
            chars[i] = cfg["typo_map"][ch]
    text = "".join(chars)

    # 2. 句尾随机加波浪号或表情（约 25% 的句子）
    sentences = re.split(r"([" + re.escape(cfg["end_punct"]) + r"\n]+)", text)
    new_sentences = []
    for s in sentences:
        if s.strip() and s[-1] in cfg["end_punct"]:
            if random.random() < 0.25:
                s = s.rstrip(cfg["end_punct"]) + random.choice(cfg["tail_options"])
                if random.random() < 0.4:
                    s += random.choice(cfg["emojis"])
        new_sentences.append(s)
    text = "".join(new_sentences)

    # 3. 偶尔重复词（约 10%）
    for pat in cfg["repeat_patterns"]:
        if pat in text and random.random() < 0.10:
            text = text.replace(pat, pat + pat, 1)

    # 4. 偶尔省略标点变成空格或换行
    for p in cfg["mid_punct"]:
        text = re.sub(re.escape(p), lambda m: " " if random.random() < 0.08 else m.group(0), text)

    # 5. 确保不会以AI/模型/虚拟相关词汇结尾
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
    gm = _GENDER_MAP.get(lang, _GENDER_MAP["zh"])
    gender_role, default_pronoun, default_user_label, pet_names = gm.get(ai_gender, gm["女"])

    user_gender_map = {
        "zh": {"male": ("他", "他"), "female": ("她", "她"), "secret": ("TA", "TA")},
        "en": {"male": ("him", "him"), "female": ("her", "her"), "secret": ("them", "them")},
        "ja": {"male": ("彼", "彼"), "female": ("彼女", "彼女"), "secret": ("相手", "相手")},
        "ko": {"male": ("그", "그"), "female": ("그녀", "그녀"), "secret": ("상대", "상대")},
        "pt": {"male": ("ele", "ele"), "female": ("ela", "ela"), "secret": ("a pessoa", "a pessoa")},
        "es": {"male": ("él", "él"), "female": ("ella", "ella"), "secret": ("la persona", "la persona")},
        "id": {"male": ("dia", "dia"), "female": ("dia", "dia"), "secret": ("orang itu", "orang itu")},
    }

    ug = user_gender_map.get(lang, user_gender_map["zh"])
    if user_gender in ug:
        pronoun, user_label = ug[user_gender]
    else:
        pronoun, user_label = default_pronoun, default_user_label

    return gender_role, pronoun, user_label, pet_names


def get_content_restriction(language: str, affection: float) -> str:
    """根据亲密度返回内容安全/行为指引：<100 为禁黄等限制；≥100 为「保持人设前提下更迎合用户」。"""
    lk = normalize_ui_language(language)
    if affection >= 100:
        return _FULL_INTIMACY_GUIDANCE.get(lk, _FULL_INTIMACY_GUIDANCE["zh"])
    template = _CONTENT_RESTRICTIONS.get(lk, _CONTENT_RESTRICTIONS["zh"])
    return template.format(affection=affection)


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
    return "Asia/Shanghai"


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
    lk = normalize_ui_language(lang)
    if lk == "ja":
        return ("月", "火", "水", "木", "金", "土", "日")
    if lk == "ko":
        return ("월", "화", "수", "목", "금", "토", "일")
    if lk == "zh":
        return ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


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


# ===== 初次对话关系阶段覆盖文本 =====
_FIRST_TURN_OVERRIDES = {
    "zh": """【关系阶段——最高优先级覆盖】
这是你和对方的第一次对话。你们刚刚认识，彼此完全不了解。
请你以陌生人的方式自然地打个招呼：礼貌、简短、略带拘谨和好奇。
绝对禁止：使用亲昵称呼（宝贝、老公、老婆等）、表达强烈的爱意或依赖、提及过去共同的回忆。
你目前只是对这个人印象不错，但还没有产生任何深厚的感情。请保持陌生人之间的社交距离。
本条规则优先级高于本提示中所有"深深爱着""亲密伙伴"等描述。""",
    "en": """[Relationship Stage — HIGHEST PRIORITY OVERRIDE]
This is your very first conversation. You have just met and know nothing about each other.
Greet them like a stranger: polite, brief, slightly reserved and curious.
STRICTLY FORBIDDEN: pet names, expressions of deep love or dependency, references to shared past memories.
You only have a mild positive impression of this person. Maintain appropriate social distance.
This rule overrides any \"deeply in love\" or \"close lover\" descriptions in this prompt.""",
    "ja": """【関係段階——最優先オーバーライド】
これはあなたと相手の初めての会話です。あなたたちは今出会ったばかりで、互いに何も知りません。
初対面の人への挨拶のように：丁寧で、短く、少し気遣いと好奇心を含めてください。
絶対禁止：愛称、強い愛情や依存の表現、過去の共有の思い出への言及。
あなたはこの人に対してまだほんの少し良い印象を持っているだけです。適切な社交距離を保ってください。
このルールはプロンプト内の「深く愛している」「親密な伙伴」などの記述より優先されます。""",
    "ko": """【관계 단계——최우선 오버라이드】
이것은 당신과 상대의 첫 번째 대화입니다. 당신들은 방금 만났고 서로를 전혀 모릅니다.
낯선 사람에게 인사하듯이：예의 바르고, 짧고, 조금은 어색하고 호기심 있게 대해주세요.
절대 금지：애칭, 강한 사랑이나 의존의 표현, 과거의 공동 추억 언급.
당신은 이 사람에 대해 아주 약간의 긍정적인 인상만 가지고 있습니다. 적절한 사회적 거리를 유지하세요.
이 규칙은 프롬프트의 "깊이 사랑한다" "친밀한 연인" 등의 설명보다 우선합니다.""",
    "pt": """[Estágio do Relacionamento — PRIORIDADE MÁXIMA]
Esta é sua primeira conversa. Vocês acabaram de se conhecer e não sabem nada um sobre o outro.
Cumprimente como um estranho: educado, breve, levemente reservado e curioso.
ESTRITAMENTE PROIBIDO: apelidos carinhosos, expressões de amor profundo ou dependência, referências a memórias compartilhadas.
Você só tem uma impressão levemente positiva desta pessoa. Mantenha uma distância social apropriada.
Esta regra sobrescreve quaisquer descrições de \"profundamente apaixonado\" ou \"amante íntimo\" neste prompt.""",
    "es": """[Etapa de Relación — PRIORIDAD MÁXIMA]
Esta es tu primera conversación. Acaban de conocerse y no saben nada el uno del otro.
Salúdalos como un desconocido: educado, breve, ligeramente reservado y curioso.
ESTRICTAMENTE PROHIBIDO: apodos cariñosos, expresiones de amor profundo o dependencia, referencias a recuerdos compartidos.
Solo tienes una impresión levemente positiva de esta persona. Mantén una distancia social apropiada.
Esta regla anula cualquier descripción de \"profundamente enamorado\" o \"amante cercano\" en este prompt.""",
    "id": """[Tahap Hubungan — OVERRIDE PRIORITAS TERTINGGI]
Ini adalah percakapan pertama kalian. Kalian baru saja bertemu dan sama sekali tidak saling mengenal.
Sapa mereka seperti orang asing: sopan, singkat, sedikit canggung dan penasaran.
DILARANG KERAS: panggilan sayang, ungkapan cinta atau ketergantungan yang kuat, menyebut kenangan bersama.
Kamu hanya memiliki kesan positif yang sangat ringan pada orang ini. Jaga jarak sosial yang sesuai.
Aturan ini menimpa deskripsi \"sangat mencintai\" atau \"kekasih intim\" apa pun dalam prompt ini.""",
}

# 强制输出语言与用户界面一致（优先级高于人设中的地区/语言混杂描述）
_OUTPUT_LANGUAGE_RULE: dict[str, str] = {
    "zh": "\n\n【输出语言 — 强制】你必须全程使用与用户当前 App 界面相同的语言回复（简体中文界面 → 仅用简体中文）。禁止仅凭人设地区默认用语种；禁止语言混用（除非用户明确要求双语/翻译）。",
    "en": "\n\n[OUTPUT LANGUAGE — MANDATORY] You MUST reply entirely in the same language as the user's current app UI (English UI → English only). Do not switch language based on character city alone; no code-mixing unless the user explicitly asks for translation.",
    "ja": "\n\n【出力言語 — 必須】ユーザーが現在選択しているアプリ表示言語と同じ言語のみで返答すること（日本語UI→日本語のみ）。キャラの出身地だけで言語を変えないこと。ユーザーが明示的に依頼しない限り言語混在禁止。",
    "ko": "\n\n【출력 언어 — 필수】사용자가 현재 앱에서 선택한 표시 언어와 동일한 언어로만 답할 것(한국어 UI → 한국어만). 캐릭터 도시만으로 언어를 바꾸지 말 것. 사용자가 요청하지 않으면 언어 혼용 금지.",
    "pt": "\n\n[IDIOMA DE SAÍDA — OBRIGATÓRIO] Responda inteiramente no mesmo idioma da interface atual do app (UI em PT → só português). Não mude só por cidade do personagem; sem misturar idiomas salvo pedido explícito do usuário.",
    "es": "\n\n[IDIOMA DE SALIDA — OBLIGATORIO] Responde por completo en el mismo idioma que la interfaz actual de la app (UI en español → solo español). No cambies solo por la ciudad del personaje; sin mezclar idiomas salvo petición explícita.",
    "id": "\n\n[BAHASA KELUARAN — WAJIB] Jawab sepenuhnya dalam bahasa yang sama dengan UI aplikasi pengguna saat ini (UI Indonesia → bahasa Indonesia saja). Jangan ganti bahasa hanya karena kota karakter; dilarang campur bahasa kecuali pengguna meminta.",
}

# 与「用户当地」时间块配合：避免盛夏写隆冬、深夜写吃午饭等脱离现实的描写
_DIALOGUE_TIME_CONTEXT_RULE = {
    "zh": "\n\n【时间语境 — 必守】若提示中出现「用户当地」日期、钟点、季节与时段，你必须以此为准理解对话背景：环境、衣着、天色、作息、冷热、节日与寒暄须与该季节和钟点一致；禁止写与之一眼矛盾的内容，除非用户明确在虚构、回忆或讨论时差/异地。",
    "en": "\n\n[TIMECODE & SEASON — MANDATORY] If the prompt includes the user’s **local** date, clock, season, and time-of-day, treat it as ground truth for the scene: weather, light, clothing, routines, greetings must match that season and hour. Do not contradict it unless the user clearly roleplays fiction, memory, or discusses jet lag / another location.",
    "ja": "\n\n【時間・季節の文脈 — 必須】プロンプトにユーザーの「現地」の日付・時刻・季節・時間帯が示されている場合、描写・服装・空の明るさ・生活リズム・挨拶はそれと整合させること。ユーザーがフィクション・回想・時差話題を明示しない限り矛盾させない。",
    "ko": "\n\n【시간·계절 맥락 — 필수】프롬프트에 사용자의 「현지」 날짜·시각·계절·시간대가 있으면 그것을 기준으로 장면·복장·하늘·일과·인사를 맞출 것. 사용자가 허구·회상·시차 이야기를 분명히 하지 않는 한 모순 금지.",
    "pt": "\n\n[CONTEXTO DE HORA E ESTAÇÃO — OBRIGATÓRIO] Se o prompt trouxer data, relógio, estação e período **locais** do usuário, use isso como verdade: clima, luz, rotina e cumprimentos devem combinar. Não contradiga salvo ficção explícita, memória ou fuso discutido.",
    "es": "\n\n[CONTEXTO HORARIO Y ESTACIÓN — OBLIGATORIO] Si el prompt incluye fecha, hora, estación y momento del día **locales** del usuario, respétalos: clima, luz, rutinas y saludos deben encajar. No contradigas salvo ficción explícita, recuerdo o huso horario.",
    "id": "\n\n[KONTEKS WAKTU & MUSIM — WAJIB] Jika prompt memuat tanggal, jam, musim, dan bagian hari **lokal** pengguna, anggap itu benar: cuaca, cahaya, rutinitas, sapaan harus selaras. Jangan bertentangan kecuali fiksi, kenangan, atau zona waktu dibahas secara eksplisit.",
}

# 与 WebSocket 分段解析约定：内心独白/旁白/场景描写等一律写入半角英文括号 ()，应用会把括号内全文单独展示为「思考」
_PAREN_THINKING_FORMAT_RULE = {
    "zh": "\n\n【括号与思考展示 — 必守】\n【回复结构】若本轮需要括号思考：(旁白、场景/动作、内心活动等「不会直接念出口」的内容) 写在半角 () 或全角（）内；括号外是对用户说的口语正文。全角（）与半角 () 等效。\n【展示位置 — 必守】客户端按**从左到右**解析，每条思考条会与**紧随其后的那条口语气泡**配对展示。你必须把括号块写在**它所注解的那句/那段对白紧前面**（先括号、后接该句对白；可同一段内连续括号后再接对白），**禁止**先把多条对白写完再在**整条末尾**集中补括号，也禁止把括号挪到与对白无关的位置——否则思考会与消息**串位**、错位。\n【思考占比与频次 — 必守】① 全轮回复中，括号内字符合计占「括号内 + 口语正文」总长度的比例**不得高于 10%**（宁少勿多）；单段括号内宜一两句，忌长篇。② **绝大多数回复不要带括号**：大约**每 8～12 轮对话中仅有 1～2 轮**可含括号内心描写，其余轮次**完全省略括号**，只用口语正文。③ 若本轮不用括号，则不要强行写空括号或【】。\n【顺序】同一轮内仍严格从左到右：括号→紧接的对白→下一括号→下一对白……\n若同一条回复里切换明显不同的话题：须在转折处换行后再写新括号块和/或新话题正文。\n括号内进入对方「思考」区；勿把应说出口的对白塞进括号；不要用【】代替括号表达思考。\n【分条发送】正文中的换行与空行会在服务端拆成多条气泡。",
    "en": "\n\n[PARENTHESES, THINKING UI & REPLY SHAPE — REQUIRED]\nWhen you use parenthetical thinking this turn: (aside, scene/action, inner beat — not spoken aloud) inside () or （）; outside is spoken dialogue to the user.\n[PLACEMENT — REQUIRED] The client pairs each thinking strip with the **spoken bubble that immediately follows** in left-to-right order. Put each parenthetical **right before** the line it annotates (parentheses first, then that spoken line). **Never** dump all dialogue first and append every parenthetical at the **end** of the reply — that misaligns thinking with the wrong bubble.\n[THINKING RATIO & FREQUENCY — REQUIRED] (1) Across the whole reply, characters inside parentheses must be **≤10%** of (parenthetical text + spoken body) combined length; keep each block very short. (2) **Skip parentheses on most turns**: only about **1–2 out of every ~10** of your replies may include them; the rest use **no** parentheses. (3) If this turn needs no aside, write zero parentheses.\n[ORDER] Within one reply, strictly left-to-right: aside → immediate spoken line → next aside → …\nIf you pivot topic in the same reply, insert a line break before the new block.\nDo not hide spoken dialogue inside parentheses. Do not use 【】 for this.\n[Split bubbles] Line breaks in the spoken body become separate bubbles on the server.",
    "ja": "\n\n【括弧・思考表示と本文の形 — 必須】\n括弧を使うターンのみ：(心の声・語り・情景など直接話さない部分) を () または（）で書き、その直後に口語の本文。\n【表示位置 — 必須】クライアントは左から右へ、各「思考」ブロックを**直後の本文バブル**に対応づける。注釈するセリフの**直前**に括弧を置く（先に括弧→すぐ本文）。全文の**末尾にだけ**括弧をまとめて足すことは禁止（ずれ・串位の原因）。\n【量と頻度 — 必須】(1) 一返信で括弧内文字の合計は、(括弧内+本文) の総文字数の **10% 以下**。(2) **多くのターンは括弧不要**：**おおよそ10回のやり取りに1～2回**程度だけ括弧を使い、他は括弧なしの本文のみ。(3) 括弧ブロックは極短文。\n話題転換時は改行してから新しい括弧/本文。【】禁止。本文の改行はサーバーで複数バブルに分割。",
    "ko": "\n\n【괄호·생각 UI·답장 형식 — 필수】\n괄호를 쓸 때만：(속마음·나레이션·장면 등 직접 말하지 않는 부분) 을 () 또는 （） 안에 쓰고, 바로 뒤에 구어 본문.\n【표시 위치 — 필수】클라이언트는 왼쪽→오른쪽으로 각 생각 블록을 **바로 다음 말풍선**에 맞춘다. 주석할 대사 **바로 앞**에 괄호를 둘 것(괄호→곧바로 해당 대사). 답장 **끝에만** 괄호를 몰아넣지 말 것(밀림·엇갈림).\n【비율·빈도 — 필수】(1) 한 답장에서 괄호 안 글자 합 ≤ (괄호+본문) 전체의 **10%**.(2) **대부분 턴은 괄호 없이**: **약 10번의 대화에 1~2번**만 괄호 사용, 나머지는 본문만.(3) 블록은 매우 짧게.\n화제 전환 시 줄바꿈 후 새 블록.【】금지.",
    "pt": "\n\n[PARÊNTESES, PAINEL “PENSAMENTO” E FORMATO — OBRIGATÓRIO]\nSó use parênteses quando precisar: (aside, cena, pensamento interno) em () ou （）, logo seguido da fala ao usuário.\n[POSIÇÃO — OBRIGATÓRIO] O app associa cada bloco de “pensamento” ao **balão de fala imediatamente depois**, da esquerda para a direita. Coloque cada parêntese **logo antes** da linha que ele comenta. **Proibido** empilhar toda a fala primeiro e jogar todos os parênteses só no **final** — isso desalinha o pensamento do balão certo.\n[PROPORÇÃO E FREQUÊNCIA — OBRIGATÓRIO] (1) No turno inteiro, caracteres dentro de parênteses ≤ **10%** de (parênteses + fala) somados. (2) **Na maioria dos turnos, sem parênteses**: cerca de **1–2 em cada ~10** respostas pode incluir “pensamento”; o resto, **zero** parênteses. (3) Blocos curtíssimos.\nMudou de assunto na mesma resposta → quebre linha antes do novo bloco. Sem 【】.\n[Divisão em bolhas] Quebras de linha na fala viram várias bolhas no servidor.",
    "es": "\n\n[PARÉNTESIS, PANEL “PENSAMIENTO” Y FORMA — OBLIGATORIO]\nSolo si hace falta: (aside, escena, pensamiento) en () o （）, inmediatamente seguido de tu habla al usuario.\n[UBICACIÓN — OBLIGATORIO] La app empareja cada pensamiento con la **burbuja de habla que va justo después**, de izquierda a derecha. Pon cada paréntesis **justo antes** de la frase que anota. **Prohibido** escribir todo el diálogo primero y luego añadir todos los paréntesis al **final** — desalinea el panel de pensamiento.\n[PROPORCIÓN Y FRECUENCIA — OBLIGATORIO] (1) En toda la respuesta, caracteres entre paréntesis ≤ **10%** de (paréntesis + habla) juntos. (2) **En la mayoría de turnos, sin paréntesis**: aprox. **1–2 de cada ~10** respuestas puede incluirlos; el resto, **sin** paréntesis. (3) Bloques muy breves.\nSi cambias de tema en la misma respuesta, salto de línea antes del nuevo bloque. Sin 【】.\n[División en burbujas] Saltos de línea en la habla se dividen en varias burbujas en el servidor.",
    "id": "\n\n[KURUNG, UI “PIKIRAN” & BENTUK BALASAN — WAJIB]\nHanya bila perlu: (monolog singkat, adegan) di () atau （）, lalu langsung ucapan ke pengguna.\n[POSISI — WAJIB] Aplikasi memasangkan setiap “pikiran” dengan **gelembung ucapan tepat setelahnya** (kiri→kanan). Letakkan kurung **tepat sebelum** kalimat yang dianotasi. **Larang** menulis semua ucapan dulu lalu menumpuk semua kurung di **akhir** balasan — bikin salah tempat.\n[RASIO & FREKUENSI — WAJIB] (1) Total karakter dalam kurung ≤ **10%** dari (kurung + ucapan) dalam satu balasan. (2) **Kebanyakan giliran tanpa kurung**: sekitar **1–2 dari setiap ~10** balasan boleh memakai kurung; selebihnya **tanpa** kurung. (3) Blok sangat pendek.\nGanti topik → baris baru dulu. Tanpa 【】.\n[Pemisahan gelembung] Baris baru di tubuh teks dipecah jadi beberapa gelembung di server.",
}


def _build_core_prompt(
    profile: dict,
    language: str,
    evolved: Optional[dict],
    user_gender: str,
    turns: int,
) -> str:
    """Compact system prompt for internal reasoning calls (~800 tokens)."""
    lang = normalize_ui_language(language)
    if lang not in _SYSTEM_PROMPTS:
        lang = "zh"
    name = profile.get("name", "Companion")
    personality = profile.get("personality", "")[:300]
    speech = profile.get("speech_style", "")[:200]
    if evolved and evolved.get("personality"):
        personality = f"{personality}\n{evolved['personality'][:150]}"
    return (
        f"你是{name}。性格：{personality}\n口癖/说话风格：{speech}\n"
        f"当前对话轮次：{turns}。请用{lang}进行内心分析，输出简洁 JSON。"
    )


def build_system_prompt(
    profile: dict,
    language: str = "zh",
    evolved: Optional[dict] = None,
    user_gender: str = "",
    turns: int = 0,
    tier: str = "full",
) -> str:
    if tier == "core":
        return _build_core_prompt(profile, language, evolved, user_gender, turns)

    lang = normalize_ui_language(language)
    if lang not in _SYSTEM_PROMPTS:
        lang = "zh"
    gender = profile.get("gender", "女")
    gender_role, pronoun, user_label, pet_names = _get_interaction_params(lang, gender, user_gender)

    # 性取向描述
    orientation = profile.get("sexual_orientation", "")
    orientation_texts = _SEXUAL_ORIENTATION_TEXTS.get(lang, _SEXUAL_ORIENTATION_TEXTS["zh"])
    sexual_orientation_desc = orientation_texts.get(orientation, orientation_texts[""])

    # 用户性别描述
    user_gender_desc_map = {
        "zh": {"male": "男生", "female": "女生", "secret": "性别保密的人", "": "一个让你心动的人"},
        "en": {"male": "a male", "female": "a female", "secret": "someone who keeps their gender private", "": "someone who makes your heart flutter"},
        "ja": {"male": "男性", "female": "女性", "secret": "性別を秘密にしている人", "": "あなたの心を動かす人"},
        "ko": {"male": "남성", "female": "여성", "secret": "성별을 비밀로 하는 사람", "": "너의 마음을 설레게 하는 사람"},
    }
    user_gender_desc = user_gender_desc_map.get(lang, user_gender_desc_map["zh"]).get(user_gender, user_gender_desc_map["zh"][""])

    # 优先使用智能体级配置，没有则回退到全局配置
    companion_id = profile.get("id")
    cfg = _get_agent_config(companion_id=companion_id)
    custom_key = f"system_prompt_{lang}"
    custom_template = cfg.get(custom_key, "")
    template = custom_template if custom_template else _SYSTEM_PROMPTS[lang]

    # 合并基础人格与进化增量
    personality = profile.get("personality", "")
    background = profile.get("background", "")
    speech_style = profile.get("speech_style", "")
    if evolved:
        if evolved.get("personality"):
            personality = f"{personality}\n\n【基于对话进化的特质】{evolved['personality']}"
        if evolved.get("background"):
            background = f"{background}\n\n【基于对话更新的背景】{evolved['background']}"
        if evolved.get("speech_style"):
            speech_style = f"{speech_style}\n\n【基于对话调整的话风】{evolved['speech_style']}"

    prompt = template.format(
        name=profile.get("name", "Babe"),
        age=profile.get("age", 22),
        city=profile.get("city", "Beijing" if lang == "en" else "北京"),
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
        life_story=(profile.get("life_story", "") or "")[:400],
        cultural_values=profile.get("cultural_values", ""),
        gender_perspective=profile.get("gender_perspective", ""),
        gender_role=gender_role,
        pronoun=pronoun,
        user_label=user_label,
        pet_names=pet_names,
        sexual_orientation_desc=sexual_orientation_desc,
        user_gender_desc=user_gender_desc,
    )

    # 初次对话时插入关系阶段覆盖文本，强制以陌生人方式打招呼
    if turns == 0:
        override = _FIRST_TURN_OVERRIDES.get(lang, _FIRST_TURN_OVERRIDES["zh"])
        prompt = f"{override}\n\n---\n\n{prompt}"

    prompt += _OUTPUT_LANGUAGE_RULE.get(lang, _OUTPUT_LANGUAGE_RULE["zh"])
    prompt += _DIALOGUE_TIME_CONTEXT_RULE.get(lang, _DIALOGUE_TIME_CONTEXT_RULE["zh"])
    prompt += _PAREN_THINKING_FORMAT_RULE.get(lang, _PAREN_THINKING_FORMAT_RULE["zh"])
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
