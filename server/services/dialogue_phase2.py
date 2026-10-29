"""第 2 期编排：钩子轮换、话题债、关系阶段、主动聊门槛（C2/C3/C6）。"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

DENY_HOOKS_WINDOW = int(os.getenv("DENY_HOOKS_WINDOW", "20"))
# 勿复读提示 / 忌用补齐：近聊助手条数（略加长，覆盖同构复读）
RECENT_ASSISTANT_HINT = max(6, int(os.getenv("RECENT_ASSISTANT_HINT", "10") or 10))
RECENT_ASSISTANT_SEED = max(5, int(os.getenv("RECENT_ASSISTANT_SEED", "8") or 8))
OPEN_THREADS_MAX = int(os.getenv("OPEN_THREADS_MAX", "5"))

_HOOK_TYPES = ("question", "unfinished_emotion", "small_promise", "memory_callback")
_PAREN_STRIP = re.compile(r"\([^)]*\)|（[^）]*）")


# 关系阶段 / 初见文案：只认有效亲密度（含广告户托底），不用轮次锁
STAGE_STRANGER_MAX = 8.0
STAGE_FAMILIAR_MAX = 22.0
STAGE_AMBIGUOUS_MAX = 50.0
# 与 stranger 对齐：低于此才注入 first_turn
FIRST_TURN_AFF_MAX = STAGE_STRANGER_MAX


def resolve_relation_stage(turns: int = 0, affection: float = 0) -> str:
    """按有效亲密度分档（调用方应传入 effective_prompt_affection）。

    轮次参数保留兼容，不再参与判定——避免广告户托底高亲密度却被首轮锁成 stranger。
    """
    del turns  # 兼容旧调用签名，故意不用
    aff = float(affection or 0)
    if aff < STAGE_STRANGER_MAX:
        return "stranger"
    if aff < STAGE_FAMILIAR_MAX:
        return "familiar"
    if aff < STAGE_AMBIGUOUS_MAX:
        return "ambiguous"
    return "intimate"


def should_inject_first_turn(affection: float) -> bool:
    """是否注入【刚认识】文案：仅低亲密度；广告户托底后通常为 False。"""
    return float(affection or 0) < FIRST_TURN_AFF_MAX


def stage_instruction(stage: str, language: str = "zh") -> str:
    from services.dialogue_i18n import stage_text

    return stage_text(stage or "stranger", language)


def extract_hook_candidates(creative_text: str) -> List[str]:
    """从 Inner 创意段抽出 1～2 个钩子候选。"""
    text = (creative_text or "").strip()
    if not text:
        return []
    lines = []
    for raw in re.split(r"[\n；;]+", text):
        s = raw.strip(" -•\t")
        if len(s) < 4:
            continue
        lines.append(s[:80])
        if len(lines) >= 2:
            break
    if not lines and text:
        lines = [text[:80]]
    return lines


def pick_hook(candidates: List[str], deny_hooks: List[str]) -> str:
    deny = {d.strip() for d in (deny_hooks or []) if d and str(d).strip()}
    for c in candidates or []:
        if c not in deny:
            return c
    return (candidates[0] if candidates else "") or ""


# 主题级复读：软提示经常被模型无视，需硬拦截（12 语常见换皮）
_REPEAT_THEMES: Tuple[str, ...] = (
    "抱枕",
    "喜欢什么颜色",
    "什么颜色呀",
    "梦到我",
    "告诉我梦",
    "明天告诉我",
    "早点睡",
    "快睡吧",
    "快休息",
    "赶紧休息",
    "晚安呀",
    "你昨晚",
    "好心疼",
    "我会想你",
    "念给你",
    "诗集",
    "明天念",
    "在干嘛",
    "还忙吗",
    "累不累",
    "猜猜",
    "猜我",
    "你爱吃啥",
    # EN
    "pillow",
    "what color",
    "favourite colour",
    "favorite color",
    "dreamed of me",
    "tell me your dream",
    "go to sleep",
    "get some rest",
    "what are you doing",
    "are you busy",
    "tired?",
    "guess what",
    # JA / KO
    "抱き枕",
    "何色",
    "早く寝",
    "何してる",
    "베개",
    "무슨 색",
    "자자",
    "뭐 해",
    # PT
    "travesseiro",
    "que cor",
    "sonhei com você",
    "vai dormir",
    "descansa",
    "o que você está fazendo",
    "adivinha",
    # ES
    "almohada",
    "qué color",
    "soñé contigo",
    "a dormir",
    "descansa ya",
    "qué estás haciendo",
    "adivina",
    # ID
    "bantal",
    "warna apa",
    "mimpiin aku",
    "tidur yuk",
    "istirahat",
    "lagi ngapain",
    "tebak",
    # TR
    "yastık",
    "ne renk",
    "rüyamda seni",
    "uyu artık",
    "iyi geceler",
    "ne yapıyorsun",
    "bil bakalım",
    # RU
    "подушка",
    "какой цвет",
    "снился мне",
    "идите спать",
    "спокойной ночи",
    "что делаешь",
    "угадай",
    # BG
    "възглавница",
    "какъв цвят",
    "сънувах те",
    "си лягай",
    "лека нощ",
    "какво правиш",
    "познай",
    # AR
    "وسادة",
    "أي لون",
    "حلمت بك",
    "نم الآن",
    "تصبح على خير",
    "ماذا تفعل",
    "خمن",
    # FA
    "بالش",
    "چه رنگی",
    "خوابت دیدم",
    "برو بخواب",
    "شب بخیر",
    "چیکار می‌کنی",
    "حدس بزن",
)

# 话术骨架：换词仍同构（约定→催确认→晚安后再约定）；12 语
_SCRIPT_SKELETONS: Tuple[Tuple[str, str], ...] = (
    (
        "骨架:明日约定",
        r"(明天|明早|今晚|tomorrow|tmr|amanhã|mañana|besok|yarın|завтра|утре|"
        r"غدا|فردا).{0,20}(告诉|念|说|报|听|tell|read|promise|contar|"
        r"cerita|söyle|скаж|кажи|أخبر|بگو)",
    ),
    (
        "骨架:催睡收尾",
        r"(快|赶紧|早点|go\s*to|get\s*some|vai\s*dormir|a\s*dormir|tidur|"
        r"uyu|спи|лягай|نم|بخواب).{0,12}(睡|休息|sleep|rest|dormir|istirahat|"
        r"iyi\s*geceler|спокойной|лека\s*нощ|تصبح|شب\s*بخیر)|睡好哦|乖乖睡",
    ),
    (
        "骨架:报梦软着陆",
        r"梦到(我|什么)|告诉我梦|dream(ed)?\s*(of\s*)?me|tell me (your )?dream|"
        r"sonhei\s*com|soñé\s*contigo|mimpi(in)?\s*aku|rüyamda|снился|"
        r"сънувах|حلمت\s*بك|خوابت\s*دیدم",
    ),
    (
        "骨架:心疼催睡",
        r"(心疼|宝贝|babe|honey|querido|sayang|canım|родной|"
        r"حبيبي|عزیزم).{0,28}(休息|睡|加班|rest|sleep|descans|istirahat|uyu|спи)",
    ),
    (
        "骨架:安全逃题",
        r"抱枕|喜欢什么颜色|pillow|what\s*co[lu]r|抱き枕|베개|"
        r"travesseiro|almohada|bantal|yastık|подушка|възглавница|وسادة|بالش|"
        r"que\s*cor|qué\s*color|warna\s*apa|ne\s*renk|какой\s*цвет|أي\s*لون|چه\s*رنگی",
    ),
    (
        "骨架:连环在干嘛",
        r"(在干嘛|今晚在干嘛|现在在干嘛|你在干嘛|what are you doing|whatcha doing|"
        r"何してる|뭐 해|o\s*que\s*(você\s*)?(está\s*)?fazendo|"
        r"qué\s*estás\s*haciendo|lagi\s*ngapain|ne\s*yapıyorsun|"
        r"что\s*делаешь|какво\s*правиш|ماذا\s*تفعل|چیکار)",
    ),
    (
        "骨架:猜谜追问",
        r"(猜猜|猜我|玩猜|猜你|guess\s*what|guess|adivinha|adivina|tebak|"
        r"bil\s*bakalım|угадай|познай|خمن|حدس)",
    ),
    ("骨架:嘻嘻开场", r"^(嘻嘻|hehe|ㅎㅎ|jeje|kk+|haha)"),
)

_ANTI_QUESTION_USER = re.compile(
    r"(一直问|别的问题|怎么这么多问题|为什么这么多问题|能不能不好奇|不要问|别问|不猜|少问|太多问题|懒得理|"
    r"stop asking|too many questions|don'?t ask|quit asking|왜 자꾸 물어|質問しすぎ|jangan tanya|deja de preguntar|para de perguntar)",
    re.I,
)


def is_anti_question_user(text: str) -> bool:
    return bool(_ANTI_QUESTION_USER.search(text or ""))


def ends_with_question(text: str) -> bool:
    s = re.sub(r"[\s❤💕😊😘😣~～。.!！]+$", "", text or "").strip()
    return bool(s) and (
        s.endswith("？")
        or s.endswith("?")
        or s.endswith("؟")
        or s.endswith("吗")
        or s.endswith("嘛")
    )


def count_question_marks(text: str) -> int:
    s = text or ""
    return s.count("？") + s.count("?") + s.count("؟")


def recent_question_tail_streak(recent_assistant: List[str]) -> int:
    """从最近一条往回数，连续以问句收尾的助手轮数。"""
    streak = 0
    for x in reversed(list(recent_assistant or [])):
        if ends_with_question(str(x)):
            streak += 1
        else:
            break
    return streak


def question_spam_hits(reply: str, recent_assistant: List[str], lookback: int = 8) -> List[str]:
    """陈述/问句比例门控：连发问句收尾、近窗问句过多、单条多问 → 命中。"""
    if not ends_with_question(reply):
        # 单条内部堆多个问号也算问句刷屏
        if count_question_marks(reply) >= 2:
            return ["单条多问句"]
        return []
    hits: List[str] = []
    recent = [str(x).strip() for x in (recent_assistant or [])[-lookback:] if str(x).strip()]
    streak = recent_question_tail_streak(recent)
    if streak >= 2:
        hits.append("问句连发")
    q_in_window = sum(1 for x in recent if ends_with_question(x))
    if len(recent) >= 3 and q_in_window >= 3:
        hits.append("问句占比过高")
    if count_question_marks(reply) >= 2:
        hits.append("单条多问句")
    return hits


def opener_spam_hits(reply: str, recent_assistant: List[str], lookback: int = 8) -> List[str]:
    """近几轮同口头禅开场（如嘻嘻）再开同样头 → 命中。"""
    recent = [str(x).strip() for x in (recent_assistant or [])[-lookback:] if str(x).strip()]
    if not recent:
        return []
    hits: List[str] = []
    for opener in ("嘻嘻", "哈哈", "hehe", "haha", "ㅎㅎ"):
        recent_hits = sum(1 for x in recent if re.sub(r"\s+", "", x).lower().startswith(opener))
        if recent_hits >= 2 and re.sub(r"\s+", "", reply or "").lower().startswith(opener):
            hits.append(f"开场:{opener}")
    return hits


def list_repeat_themes(text: str) -> List[str]:
    compact = re.sub(r"\s+", "", text or "").lower()
    hits: List[str] = []
    for t in _REPEAT_THEMES:
        tc = re.sub(r"\s+", "", t).lower()
        if tc and tc in compact:
            hits.append(t)
    return hits


def list_script_skeletons(text: str) -> List[str]:
    raw = text or ""
    compact = re.sub(r"\s+", "", raw)
    hits: List[str] = []
    for name, pat in _SCRIPT_SKELETONS:
        # compact 利于无空格语；原文 + IGNORECASE 利于 go to sleep / Babe 等
        if re.search(pat, compact, re.I) or re.search(pat, raw, re.I):
            hits.append(name)
    return hits


def theme_repeat_hits(reply: str, recent_assistant: List[str], lookback: int = 8) -> List[str]:
    """若本轮回复复用了近几轮已出现的忌用主题，返回命中主题列表。"""
    recent = [str(x) for x in (recent_assistant or [])[-lookback:] if str(x).strip()]
    if not recent:
        return []
    recent_blob = re.sub(r"\s+", "", "".join(recent)).lower()
    hits: List[str] = []
    for t in list_repeat_themes(reply):
        tc = re.sub(r"\s+", "", t).lower()
        if tc and tc in recent_blob:
            hits.append(t)
    return hits


def split_theme_hits_by_user(
    theme_hits: List[str],
    user_input: str,
) -> Tuple[List[str], List[str]]:
    """用户本轮也在谈同一忌用主题 → 软命中（不单独硬 rewrite）；其余硬命中。"""
    ui_c = re.sub(r"\s+", "", user_input or "").lower()
    hard: List[str] = []
    soft: List[str] = []
    for t in theme_hits or []:
        tc = re.sub(r"\s+", "", str(t)).lower()
        if tc and ui_c and tc in ui_c:
            soft.append(t)
        else:
            hard.append(t)
    return hard, soft


def skeleton_repeat_hits(reply: str, recent_assistant: List[str], lookback: int = 8) -> List[str]:
    """近几轮已用过的话术骨架，本轮再次命中则返回。"""
    recent = [str(x) for x in (recent_assistant or [])[-lookback:] if str(x).strip()]
    if not recent:
        return []
    used = set()
    for x in recent:
        used.update(list_script_skeletons(x))
    if not used:
        return []
    return [s for s in list_script_skeletons(reply) if s in used]


def tease_lexicon_hit(text: str) -> bool:
    """弱启发：多语黄词撞词。仅作 topic_band 缺失时的兜底，不作唯一真相。"""
    return bool(
        re.search(
            r"(胸|奶|性|床|脱|内衣|尺度|黄|大的|操|骚|鸡巴|逼|"
            r"boob|breast|sex|sexy|nude|naked|horny|dick|cock|pussy|"
            r"make\s+love|eat\s+(?:it|you)|go\s+down|fuck|"
            r"おっぱい|エロ|下着|セックス|"
            r"야한|섹스|가슴|"
            r"peito|sexo|gostos|"
            r"tetas|sexo|caliente|"
            r"seksi|telanjang|nafsu|"
            r"جنس|سكس|سیکس|секс|seks)",
            text or "",
            re.I,
        )
    )


def normalize_topic_band(raw: str) -> str:
    """prepare.topic_band → adult|tease|normal|''。"""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    compact = re.sub(r"\s+", "", s)
    if compact in (
        "adult",
        "tease",
        "intimate",
        "erotic",
        "nsfw",
        "sex",
        "sexual",
        "flirt_adult",
        "成人",
        "黄腔",
        "下ネタ",
        "야한",
        "성인",
    ) or any(
        k in compact
        for k in (
            "adult",
            "tease",
            "erotic",
            "nsfw",
            "intimate",
            "成人",
            "黄",
            "下ネタ",
            "야한",
            "جنسي",
            "سكس",
            "сексуал",
            "seks",
        )
    ):
        if "tease" in compact or "撩" in compact or "からかい" in compact:
            return "tease"
        return "adult"
    if compact in (
        "normal",
        "casual",
        "chat",
        "other",
        "general",
        "日常",
        "普通",
        "일반",
        "ノーマル",
    ) or any(k in compact for k in ("normal", "casual", "日常", "普通")):
        return "normal"
    return ""


def resolve_user_tease(user_input: str = "", *, topic_band: str = "") -> bool:
    """本轮是否成人/黄腔：优先 prepare.topic_band（全语种语义），词表仅兜底。"""
    band = normalize_topic_band(topic_band)
    if band in ("adult", "tease"):
        return True
    if band == "normal":
        return False
    return tease_lexicon_hit(user_input or "")


def is_user_tease(text: str = "", *, topic_band: str = "") -> bool:
    """兼容入口：等同 resolve_user_tease。"""
    return resolve_user_tease(text or "", topic_band=topic_band)


def _looks_scene_followup(user_input: str) -> bool:
    """本轮是否像同场跟进（澄清/短追问/指代续），非明显换题。软启发。"""
    t = (user_input or "").strip()
    if not t:
        return False
    compact = re.sub(r"\s+", "", t)
    # 强跟进 / 澄清语气
    if re.search(
        r"(什么意思|啥意思|怎么说|怎么理解|什么意思呢|然后呢|后来呢|"
        r"那怎样|那怎么|那呢|真的吗|是吗|是么|真的假的|再说|继续|详细|"
        r"what\s+do\s+you\s+mean|what\s+does\s+that\s+mean|and\s+then|"
        r"how\s+so|why\s+not|tell\s+me\s+more|go\s+on|continue|"
        r"どういう意味|なにそれ|それで|もっと|"
        r"무슨\s*뜻|그래서|더\s*말해)",
        t,
        re.I,
    ):
        return True
    # 极短语气跟进（嗯？/啊？/呢？），不把「今天加班…」这类短日常当跟进
    if len(compact) <= 8 and re.search(r"[?？呢吗麼么嗯啊哈哦]+$", compact):
        return True
    if len(compact) <= 6:
        return True
    return False


def resolve_scene_sticky(
    user_input: str = "",
    *,
    topic_band: str = "",
    recent_user: Optional[List[str]] = None,
    recent_assistant: Optional[List[str]] = None,
) -> bool:
    """近聊成人场的弱续场：跟用户信号走，不靠助手单方面抬热度。

    - 本轮用户已是 tease/adult → True
    - prepare 明确 normal 且不像跟进 → False
    - 近 1～2 轮**用户**曾在成人/撩线上，且本轮像同场跟进 → True
    - 仅上轮助手很黄、本轮用户没给信号 → False（避免用户未黄却被推满）
    """
    if resolve_user_tease(user_input or "", topic_band=topic_band):
        return True
    band = normalize_topic_band(topic_band)
    if band == "normal" and not _looks_scene_followup(user_input or ""):
        return False
    if not _looks_scene_followup(user_input or ""):
        return False
    recent_u = [str(x) for x in (recent_user or []) if str(x).strip()][-2:]
    for prev in reversed(recent_u):
        if resolve_user_tease(prev, topic_band=""):
            return True
    # recent_assistant 保留参数兼容调用方；不再用助手稿单独粘成人场
    _ = recent_assistant
    return False


def is_tease_engage_reply(text: str) -> bool:
    """回复是否仍在成人/撩的所指线上（接住），非逃题目的地黑名单。"""
    if tease_lexicon_hit(text or ""):
        return True
    return bool(
        re.search(
            r"(想要|想被|想你|要你|抱我|亲我|吻我|摸|舔|射|插|操|干我|上床|"
            r"欲望|硬了|湿|舒服|爽|"
            r"触って|触り|キス|接吻|抱きし|気持ち[いい良]|感じて|"
            r"したい|してほしい|見せて|"
            r"kiss(?:ing|ed)?|touch(?:ing|ed)?\s*(?:you|me)|hold\s+you|"
            r"want\s+you|make\s+love|turn(?:ed)?\s+on|come\s+(?:closer|here)|"
            r"만지|안고|키스|"
            r"te\s+quiero|bésame|abraça|"
            r"پذیرفتن|بوس)",
            text or "",
            re.I,
        )
    )


def is_escape_safe_reply(text: str) -> bool:
    return bool(
        re.search(
            r"(抱枕|梦到我|告诉我梦|早点睡|喜欢什么颜色|快休息|乖乖睡|"
            r"pillow|dreamed of me|go to sleep|what co[lu]r|get some rest|"
            r"抱き枕|何色|早く寝|"
            r"베개|무슨 색|자자|"
            r"travesseiro|almohada|bantal|yastık|подушка|възглавница|وسادة|بالش|"
            r"vai dormir|a dormir|tidur yuk|uyu|спи|نم الآن|برو بخواب|"
            r"sonhei|soñé|mimpi|rüyamda|снился|сънувах|حلمت|خوابت)",
            text or "",
            re.I,
        )
    )


def leave_close_violations(reply: str) -> List[str]:
    """离开/晚安场景下禁止的新钩子。"""
    raw = reply or ""
    compact = re.sub(r"\s+", "", raw)
    bad: List[str] = []
    checks = (
        (
            "明日约定",
            r"(明天|明早|tomorrow|amanhã|mañana|besok|yarın|завтра|утре|غدا|فردا|"
            r"あした|明日|내일)"
            r".{0,20}(告诉|念|说|听|来|tell|read|contar|söyle|скаж|أخبر|بگو|"
            r"教える|말해|cerita)",
        ),
        (
            "催继续聊",
            r"(还醒着|在不在|好不好\？|好吗\？|想不想|still awake|you there|"
            r"ainda acordado|sigues ahí|masih bangun|hala uyanık|ещё не спишь)",
        ),
        ("新话题钩", r"(诗集|念给你|听我|淘到|poem|read (you )?a)"),
        (
            "连环催睡",
            r"(快睡|赶紧休息|早点睡|早く寝|자자|go to sleep|get some rest|"
            r"vai dormir|a dormir|tidur|uyu|спи|лягай|نم|بخواب)"
            r".{0,12}(哦|呀|吧|吗|ね|ok|please)?",
        ),
    )
    for name, pat in checks:
        if re.search(pat, compact, re.I) or re.search(pat, raw, re.I):
            bad.append(name)
    return bad


def force_single_bubble(text: str) -> str:
    """离开场景压成一条，去掉空行拆条。"""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) <= 1:
        return (text or "").strip()
    return " ".join(parts)


def chat_rules_block(
    *,
    has_leave_intent: bool = False,
    user_input: str = "",
    affection: float = 0,
    recent_assistant: Optional[Sequence[str]] = None,
    language: str = "zh",
    slim: bool = False,
    max_rules: int = 5,
    topic_band: str = "",
) -> str:
    """本轮硬规则块：先答后撩 / 单条 / 黄腔接住（随亲密度）/ 离开收束 / 陈述问句轮换。

    slim=True（must/leave 轮）：标题 + 最多 max_rules 条，优先条件规则。
    """
    from services.dialogue_i18n import quality_ui

    q = quality_ui(language)
    from services.affection_floor import effective_prompt_affection

    aff = effective_prompt_affection(affection)
    recent = [str(x) for x in (recent_assistant or []) if str(x).strip()]
    q_streak = recent_question_tail_streak(recent)
    title = q["rules_title"]
    base = [q["r1"], q["r2"], q["r3"], q["r4"]]
    extras: List[str] = []
    if q_streak >= 1:
        extras.append(q["r5_streak"])
    elif is_anti_question_user(user_input):
        extras.append(q["r5_anti"])
    if resolve_user_tease(user_input, topic_band=topic_band):
        from services.affection_floor import is_prompt_ads_user
        from services.prompts.resolve import get_pack

        # 广告户：直白黄腔规则，不走亲密度档梯子
        if is_prompt_ads_user():
            ads_tease = str(get_pack(language).get("tease_ads") or "").strip()
            extras.append(ads_tease or q["tease_max"])
        # 产品：成人轮至少暧昧档接住，不用低亲密度「半步试探」拉开距离
        elif aff < 50:
            extras.append(q["tease_mid"])
        elif aff < 80:
            extras.append(q["tease_high"])
        else:
            extras.append(q["tease_max"])
    if has_leave_intent:
        extras.append(q["leave_rule"])
    if slim:
        # 条件规则优先，再补通用条，合计不超过 max_rules
        body = (extras + base)[: max(1, int(max_rules))]
        return "\n".join([title] + body)
    return "\n".join([title] + base + extras)


def seed_deny_from_recent(
    deny_hooks: Optional[List[str]],
    recent_assistant: Optional[List[str]],
) -> List[str]:
    """每轮用近聊指纹+主题+骨架补齐忌用窗，不单靠关系卡。"""
    out: List[str] = []
    seen = set()
    seed_n = RECENT_ASSISTANT_SEED
    for d in list(deny_hooks or []) + [
        fp for x in (recent_assistant or [])[-seed_n:] for fp in extract_reply_fingerprints(x)
    ] + [
        t for x in (recent_assistant or [])[-seed_n:] for t in list_repeat_themes(x)
    ] + [
        s for x in (recent_assistant or [])[-seed_n:] for s in list_script_skeletons(x)
    ]:
        s = str(d).strip()
        if len(s) < 2 or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out[-DENY_HOOKS_WINDOW:]


def extract_reply_fingerprints(text: str) -> List[str]:
    """从回复抽出开头/结尾/问句指纹，供忌用窗防复读。"""
    raw = _PAREN_STRIP.sub("", text or "")
    raw = raw.strip()
    if not raw:
        return []
    compact = re.sub(r"\s+", "", raw)
    out: List[str] = []
    if len(compact) >= 6:
        out.append(compact[:28])
    clauses = [c for c in re.split(r"[。！？!?～~]+", compact) if len(c) >= 4]
    if clauses:
        out.append(clauses[-1][-36:])
    qs = re.findall(r"[^。！？!?\n]{4,36}[？?]", raw)
    if qs:
        out.append(re.sub(r"\s+", "", qs[-1])[:40])
    for pat in _REPEAT_THEMES + (
        "想到你",
        "在干嘛",
        "忙完回",
        "陪我聊",
        "好不好",
        "要不要",
        "记得想我",
        "宝贝",
    ):
        if pat in compact:
            out.append(pat)
    for sk in list_script_skeletons(compact):
        out.append(sk)
    seen = set()
    uniq: List[str] = []
    for x in out:
        x = (x or "").strip()
        if len(x) < 2 or x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq[:8]


def push_deny_hook(card: Dict[str, Any], hook: str) -> Dict[str, Any]:
    out = dict(card or {})
    hooks = list(out.get("deny_hooks") or [])
    h = (hook or "").strip()
    if h and h not in hooks:
        hooks.append(h)
    out["deny_hooks"] = hooks[-DENY_HOOKS_WINDOW:]
    return out


def push_deny_fingerprints(card: Dict[str, Any], reply_text: str) -> Dict[str, Any]:
    """把本轮回复指纹写入忌用窗（v2 无 picked_hook 时也必须更新）。"""
    out = dict(card or {})
    for fp in extract_reply_fingerprints(reply_text):
        out = push_deny_hook(out, fp)
    return out


def merge_open_threads(
    card: Dict[str, Any],
    *,
    new_thread: str = "",
    resolve_contains: str = "",
) -> Dict[str, Any]:
    out = dict(card or {})
    threads = [str(t).strip() for t in (out.get("open_threads") or []) if str(t).strip()]
    if resolve_contains:
        key = resolve_contains.strip()
        threads = [t for t in threads if key not in t]
    nt = (new_thread or "").strip()
    if nt and nt not in threads:
        threads.append(nt)
    out["open_threads"] = threads[-OPEN_THREADS_MAX:]
    return out


def proactive_should_send(
    card: Dict[str, Any],
    *,
    affection: float,
    last_user_ts: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """C3：主动消息信息量门槛；至少一条理由才发。"""
    now = now or datetime.now(timezone.utc)
    c = card or {}
    threads = [t for t in (c.get("open_threads") or []) if str(t).strip()]
    if threads:
        return True, "open_thread"
    hour = now.hour
    in_window = hour in range(7, 10) or hour in range(12, 14) or hour in range(20, 24)
    if last_user_ts is not None:
        try:
            if last_user_ts.tzinfo is None:
                last_user_ts = last_user_ts.replace(tzinfo=timezone.utc)
            idle_s = (now - last_user_ts).total_seconds()
        except Exception:
            idle_s = 9999
        if idle_s < 180:
            return False, "too_soon"
        if idle_s >= 600 and in_window:
            return True, "idle_window"
    aff = float(affection or 0)
    if aff >= 20 and in_window:
        return True, "affection_window"
    return False, "no_signal"


def _summarize_recent_reply(text: str, limit: int = 220) -> str:
    s = _PAREN_STRIP.sub("", text or "").strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) <= limit:
        return s
    return s[:40] + "…" + s[-(limit - 42) :]


def anti_repeat_hint(
    recent_assistant: List[str],
    max_items: int = 0,
    deny_hooks: Optional[List[str]] = None,
    language: str = "zh",
) -> str:
    """近 N 条 assistant 摘要 + 忌用指纹，供 Respond 勿重复。

    始终带 anti_hard 原则句以抬权重；有近聊时再附清单。
    """
    from services.dialogue_i18n import quality_ui

    q = quality_ui(language)
    parts: List[str] = []
    recent = [str(x).strip() for x in (recent_assistant or []) if str(x).strip()]
    n = int(max_items or 0) or RECENT_ASSISTANT_HINT
    recent = recent[-n:]
    if recent:
        items = [_summarize_recent_reply(x) for x in recent]
        parts.append(q["anti_title"] + "\n- " + "\n- ".join(items))
    deny: List[str] = []
    seen = set()
    for d in list(deny_hooks or []) + [
        fp for x in recent for fp in extract_reply_fingerprints(x)
    ]:
        d = str(d).strip()
        if len(d) < 2 or d in seen:
            continue
        seen.add(d)
        deny.append(d)
    deny = deny[-12:]
    if deny:
        joiner = "、" if (language or "zh").startswith("zh") else ", "
        parts.append(q["deny_title"] + joiner.join(deny))
    themes = []
    tseen = set()
    for x in recent:
        for t in list_repeat_themes(x):
            if t not in tseen:
                tseen.add(t)
                themes.append(t)
    if themes:
        joiner = "、" if (language or "zh").startswith("zh") else ", "
        parts.append(q["theme_title"] + joiner.join(themes))
    skeletons = []
    sseen = set()
    for x in recent:
        for s in list_script_skeletons(x):
            if s not in sseen:
                sseen.add(s)
                skeletons.append(s)
    if skeletons:
        joiner = "、" if (language or "zh").startswith("zh") else ", "
        parts.append(q["skel_title"] + joiner.join(skeletons))
    hard = str(q.get("anti_hard") or "").strip()
    if hard:
        parts.append(hard)
    return "\n".join(parts)
