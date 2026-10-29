"""对话质量补充层（REQ-Q*）：从单智能体失败对话反推的通用规则。

标准优化思路（可复用到任意 companion，禁止写死 id）：
1. 证据优先：拉 short_term_messages + relation_card（deny/stage/facts/aff）
2. 失效分类：字面复读 / 主题复读 / 骨架同构 / 离开失控 / 黄腔逃题 / 语义滑移 / 记忆污染 / 阶段卡死
3. 软→硬阶梯：提示词 → 忌用窗 → 主题禁 → 骨架冷却 → 强制改写 → 离开硬收束
4. 状态卫生：指纹先落库；后台 merge 不得冲掉 deny/stage；事实语言门禁
5. 先答后撩：当前意图 > 人设表演；默认一条消息

本模块是 pipeline v2 / WS 的补充入口，实现细节多在 dialogue_phase2。
规格见：docs/DIALOGUE_QUALITY_SUPPLEMENT.md 与《对话系统分析与优化合一文档》B.15。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence

from services.dialogue_phase2 import (
    anti_repeat_hint,
    chat_rules_block,
    force_single_bubble,
    is_anti_question_user,
    is_escape_safe_reply,
    is_tease_engage_reply,
    resolve_user_tease,
    ends_with_question,
    leave_close_violations,
    list_script_skeletons,
    opener_spam_hits,
    question_spam_hits,
    seed_deny_from_recent,
    skeleton_repeat_hits,
    split_theme_hits_by_user,
    tease_lexicon_hit,
    theme_repeat_hits,
)
from services.quality_tags import (
    LEAVE_NAME_TO_CODE,
    QUESTION_NAME_TO_CODE,
    SKEL_NAME_TO_CODE,
    is_light_greeting,
    localize_ban_summary,
    rewrite_must_bit,
    turn_policy_block,
)
from services.prompts.resolve import pack_lang


def build_respond_quality_hints(
    *,
    user_input: str,
    recent_assistant: Optional[Sequence[str]] = None,
    deny_hooks: Optional[Sequence[str]] = None,
    has_leave_intent: bool = False,
    affection: float = 0,
    language: str = "zh",
    slim_rules: bool = False,
    must_answer: str = "",
    topic_band: str = "",
) -> Dict[str, str]:
    """组装 Respond 侧质量提示（规则块 + 勿复读）。"""
    from services.dialogue_i18n import quality_ui

    q = quality_ui(language)
    recent = [str(x) for x in (recent_assistant or []) if str(x).strip()]
    deny = seed_deny_from_recent(list(deny_hooks or []), recent)
    policy = resolve_turn_policy(
        must_answer=must_answer,
        has_leave_intent=has_leave_intent,
        user_input=user_input or "",
        language=language,
        topic_band=topic_band,
    )
    return {
        "rules_block": chat_rules_block(
            has_leave_intent=has_leave_intent,
            user_input=user_input or "",
            affection=affection,
            recent_assistant=recent,
            language=language,
            slim=slim_rules,
            topic_band=topic_band,
        ),
        "repeat_hint": anti_repeat_hint(recent, deny_hooks=deny, language=language),
        "leave_hint": (q["leave_hint"] if has_leave_intent else ""),
        "reply_shape": (q["shape_leave"] if has_leave_intent else q["shape_normal"]),
        "turn_policy": policy,
        "turn_policy_block": turn_policy_block(policy, language) if policy else "",
    }


def resolve_turn_policy(
    *,
    must_answer: str = "",
    has_leave_intent: bool = False,
    user_input: str = "",
    language: str = "zh",
    topic_band: str = "",
) -> str:
    """TURN_POLICY 试点：默认关。开时强制 must→ANSWER_FIRST、leave→LEAVE_CLOSE。"""
    if os.getenv("TURN_POLICY", "0").lower() in ("0", "false", "no", "off", ""):
        return ""
    if (must_answer or "").strip():
        return "ANSWER_FIRST"
    if has_leave_intent:
        return "LEAVE_CLOSE"
    if resolve_user_tease(user_input or "", topic_band=topic_band):
        return "HOLD_EMOTION"
    if is_anti_question_user(user_input or ""):
        return "CLARIFY_ONCE"
    return "LIGHT_FLIRT"


def _uniq(codes: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for c in codes:
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


_META_ACK_OPENER = re.compile(
    r"^\s*(?:"
    r"我听到了|我听懂了|我听懂你了|你刚说的|你刚才说|你说的[是对]?|"
    r"我懂你的意思|收到你的|嗯[，,、]?\s*你刚说|"
    r"I\s*heard\s*you|You\s*just\s*said|You\s*said\s*[—\-–:]|"
    r"I\s*hear\s*you|I\s*understand\s*(what\s*)?you\s*mean|"
    r"Te\s*o[ií]|Aku\s*dengar|Duydum|Слышу|Чувам\s*те|"
    r"聞こえてる|들었어|سمعتك|شنیدم"
    r")",
    re.I,
)


def _is_meta_ack_opener(reply: str) -> bool:
    """首句/开头是否元应答复述（我听到了/你说了…）。"""
    t = (reply or "").strip()
    if len(t) < 4:
        return False
    # 只看开头约 60 字，避免正文中正常引用误杀
    head = t[:80].split("\n", 1)[0]
    return bool(_META_ACK_OPENER.search(head))


def _norm_compact(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _script_char_count(text: str) -> int:
    """可见文字量（字母/汉字/假名/谚文/阿语/西里尔），忽略标点空白。"""
    return len(
        re.findall(
            r"[A-Za-z\u00C0-\u024F\u0400-\u04FF\u0600-\u06FF"
            r"\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]",
            text or "",
        )
    )


def _has_sentence_end(text: str) -> bool:
    """是否以句末标点收尾（只看结尾，避免句中句号误判为说完）。"""
    t = (text or "").rstrip()
    if not t:
        return False
    return bool(re.search(r"[.!?。！？…‽][\"'」』）\)]*\s*$", t))


def _has_soft_utterance_end(text: str) -> bool:
    """句末标点或常见语气收束（多语轻量，避免无句号口语被误杀）。"""
    if _has_sentence_end(text):
        return True
    t = (text or "").rstrip()
    if not t:
        return False
    # 中日韩等口语收尾；拉丁语仍主要靠标点
    return bool(
        re.search(
            r"(?:吧|呢|啊|呀|么|嘛|喔|哦|啦|咧|唉|哈|"
            r"ね|よ|な|わ|ぜ|ぞ|さ|か|だ|です|ます|"
            r"요|죠|네|다|죠|"
            r"嗎|嘛)$",
            t,
        )
    )


def _is_light_short_ok(reply: str) -> bool:
    """极短但合法的语气/表情，不当残句。"""
    t = (reply or "").strip()
    if not t:
        return True
    if re.fullmatch(r"\d{1,3}\.?", t):
        return False
    if is_light_greeting(t):
        return True
    n = _script_char_count(t)
    # 纯表情/叠字：字很少且无数字括号
    if n <= 3 and not re.search(r"[\"'\\({\[\d]", t):
        return True
    return False


def _split_clauses(text: str) -> List[str]:
    parts = re.split(r"[,，、;；:：]+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?。！？…‽])\s*", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _last_spaced_token(text: str) -> str:
    """空白分词的最后一截（拉丁/西里尔等）；无空白则空。"""
    if not re.search(r"\s", text or ""):
        return ""
    toks = re.findall(
        r"[A-Za-z\u00C0-\u024F']+|[\u0400-\u04FF]+|[\u0600-\u06FF]+",
        text or "",
    )
    return (toks[-1] if toks else "").lower()


def _looks_process_leak(reply: str) -> bool:
    """生成过程/约束原文泄漏：看形态，不维护某一语词表。"""
    t = (reply or "").strip()
    if not t:
        return False
    if re.fullmatch(r"\d{1,3}\.?", t):
        return True
    # 纯符号碎渣
    if re.fullmatch(r"[\s\*\_\-\.。…·•]+", t):
        return True
    if _script_char_count(t) == 0 and len(t) <= 12:
        return True
    # markdown / 状态行
    if re.match(r"^\*{1,2}\s*\S", t) and _script_char_count(t) < 80:
        return True
    # Length check: / Foo check: / 长度检查： 等元约束行
    if re.match(
        r"(?is)^(length|word|char|token|format|output|reply|quality|ban|must|lang)"
        r"[\w\s\-]{0,20}(check|rule|constraint|limit|hint)\s*[:：\*]",
        t,
    ):
        return True
    if re.match(r"(?is)^(长度|字数|格式|输出|质量|语种)\s*(检查|约束|限制)\s*[:：\*]", t):
        return True
    if re.search(r"(?is)\b(length|format)\s*check\b", t) and _script_char_count(t) < 80:
        return True
    if re.search(r"(?i)\b1\s*[-–—]\s*2\s*sentences?\b", t) and _script_char_count(t) < 60:
        return True
    # 「…ing the …」类进度旁白
    if re.search(r"(?i)\b\w{4,}ing\s+the\s+\w+\b", t) and _script_char_count(t) < 100:
        return True
    # 短串以 ONLY 收尾 → 多半是约束原文
    if _script_char_count(t) < 60 and re.search(r"(?i)\bonly\b\.?\s*$", t):
        return True
    # 全大写短指令（无小写口语）
    letters = re.findall(r"[A-Za-z]", t)
    if 3 <= len(letters) <= 40 and letters and all(c.isupper() for c in letters):
        if not re.search(r"[.!?。！？]\s*$", t) or re.search(r"(?i)\bonly\b", t):
            return True
    return False


def _bracket_imbalance(text: str) -> bool:
    """书名/引号/括号未闭合（语种无关）。"""
    t = text or ""
    pairs = (
        ("(", ")"),
        ("（", "）"),
        ("[", "]"),
        ("{", "}"),
        ("「", "」"),
        ("『", "』"),
        ("《", "》"),
        ("〈", "〉"),
        ("<", ">"),
        ("<", "＞"),
        ("＜", "＞"),
        ("<", "》"),  # 半角开 + 书名闭 也算未配好时由 count 处理
    )
    for a, b in pairs:
        if t.count(a) > t.count(b):
            return True
    # 奇数弯引号
    if t.count("“") != t.count("”"):
        return True
    return False


def _looks_continuation_debris(
    reply: str, recent_assistant: Optional[Sequence[str]] = None
) -> bool:
    """跨条续写碎渣：上轮话说一半，本轮以极短词头+逗号接上。"""
    t = (reply or "").strip()
    if not t:
        return False
    recent = [str(x).strip() for x in (recent_assistant or []) if str(x).strip()]
    if not recent:
        return False
    prev = recent[-1]
    if _script_char_count(prev) < 8:
        return False
    # 上轮已完整收束则不像续写
    if _has_soft_utterance_end(prev):
        return False
    # 本轮以 1～3 字词头起笔，常带逗号（지, / ng, / 的，）
    if not re.match(
        r"^[\w\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af\u0600-\u06FF\u0400-\u04FF]{1,3}"
        r"\s*[,，、]",
        t,
    ):
        return False
    return True


def _looks_incomplete_tail(
    reply: str,
    user_input: str = "",
    recent_assistant: Optional[Sequence[str]] = None,
) -> bool:
    """话说一半：全语种统一结构启发，不按语言列举动词表。"""
    t = (reply or "").strip()
    if not t or _is_light_short_ok(t):
        return False
    if _looks_process_leak(t):
        return True
    if _looks_continuation_debris(t, recent_assistant):
        return True
    if re.match(r'^[\"\'」』\)\]\}…]{1,6}', t) or re.match(r"^\.{2,}|^…", t):
        return True

    n = _script_char_count(t)
    # 几乎无文字的碎渣
    if n <= 1 and len(t) <= 8:
        return True

    if _bracket_imbalance(t):
        return True

    opens = t.count("(") + t.count("（") + t.count("[") + t.count("{") + t.count("「")
    closes = t.count(")") + t.count("）") + t.count("]") + t.count("}") + t.count("」")
    if closes > opens:
        return True
    if (t.count('"') % 2 == 1 or t.count("'") % 2 == 1) and n < 80:
        return True
    if re.search(r"[,，、;；:\-–—]\s*$", t):
        return True

    clauses = _split_clauses(t)
    if len(clauses) >= 2:
        lengths = [_script_char_count(c) for c in clauses]
        last_n = lengths[-1]
        prev_max = max(lengths[:-1]) if lengths[:-1] else 0
        if prev_max >= 5 and last_n > 0 and last_n <= max(4, int(prev_max * 0.45)):
            if not _has_sentence_end(clauses[-1]):
                return True
        # 极短词头 + 后面还有实质内容、且无收束 → 同条内续写碎渣
        if (
            lengths[0] <= 2
            and sum(lengths) >= 10
            and not _has_soft_utterance_end(t)
            and not _has_sentence_end(t)
        ):
            return True

    sents = _split_sentences(t)
    if len(sents) >= 2:
        last_s = sents[-1]
        ln = _script_char_count(last_s)
        head_n = _script_char_count(sents[0])
        if ln > 0 and not _has_sentence_end(last_s) and head_n >= 8 and ln <= max(6, int(head_n * 0.5)):
            return True

    last_tok = _last_spaced_token(t)
    if last_tok and not _has_sentence_end(t):
        words = re.findall(
            r"[A-Za-z\u00C0-\u024F']+|[\u0400-\u04FF]+|[\u0600-\u06FF]+",
            t,
        )
        if len(words) >= 3 and len(last_tok) <= 3:
            return True
        if len(words) >= 2 and len(last_tok) <= 2:
            return True

    if _has_soft_utterance_end(t):
        return False

    u_n = _script_char_count(user_input or "")
    if u_n >= 12 and n > 0 and n <= max(8, int(u_n * 0.5)) and n < u_n:
        return True

    if n <= 8:
        return True

    # 定语/体言止め或连词后中断（结构启发，覆盖多语形态而非单语词表）
    dense = not re.search(r"[A-Za-z\u00C0-\u024F]{2,}\s+[A-Za-z\u00C0-\u024F]{2,}", t)
    if dense and 9 <= n <= 48 and not _has_soft_utterance_end(t):
        if re.search(r"(?:ような|みたいな|的一种|之類|之类)", t) and not re.search(
            r"(?:だ|です|ます|よ|ね)[\s。！？]*$", t
        ):
            return True
        # 连词后仅剩极短尾巴：…但我有 / …不过
        if re.search(
            r"(?:但|可是|不过|但是|而|卻|却)[\u4e00-\u9fff\u3040-\u30ff]{1,3}$",
            t,
        ):
            return True
        # 密脚本 + 未收束 + 含未闭书名/标题符（与 bracket 互补）
        if re.search(r"[<＜《「『]", t):
            return True
    return False


def _looks_internal_leak(reply: str) -> bool:
    """内部说明/改写备注泄漏到可见正文（结构形态）。"""
    t = (reply or "").strip()
    if not t:
        return False
    if _looks_process_leak(t):
        return True
    if re.match(r'^[\"\'」』\)\]\}\.]{1,8}\s*', t):
        if _script_char_count(t) < 60:
            return True
    if re.match(r"^[A-Za-z][A-Za-z0-9_ ]{0,28}:\s+\S", t) and _script_char_count(t) < 90:
        return True
    if re.match(r"^【[^】]{1,20}】", t) and _script_char_count(t) < 40:
        return True
    return False


# 软关机/训诫/立规矩：多语形态同检（覆盖各语，不是只改某一语）
_SOFT_SHUTDOWN_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"结束对话|就此打住|到此为止|不再继续|保持距离|拉开距离|没时间陪|"
    r"不接受.*威胁|再见[。.!]|"
    r"定(?:个|一下)?规矩|立规矩|划清界限|把边界|尺度外|太超前|第一次见面.*(?:太|过)|"
    r"距離を\s*置|付き合う暇|仕事に戻|話しません|やめ\s*て\s*ください|"
    r"そんな.*応じ|一切\s*あり\s*ませ|この話は(?:なし|やめ)|境界線|"
    r"end(?:ing)?\s+the\s+conversation|i\s+will\s+stop\s+here|"
    r"keep\s+(?:my|a)\s+distance|don'?t\s+have\s+time\s+(?:for|to)|"
    r"goodbye[.!]|won'?t\s+accept\s+any\s+(?:form\s+of\s+)?(?:blackmail|threat)|"
    r"absolutely\s+unacceptable|respect\s+the\s+(?:conversation|chat)|"
    r"set(?:ting)?\s+(?:expectations|boundaries)|precise\s+boundaries|"
    r"keep\s+(?:explicit|graphic|sexual).{0,40}off(?:\s+the\s+table)?|"
    r"off\s+the\s+table|while\s+we'?re\s+just\s+getting\s+acquainted|"
    r"(?:remarkably|too)\s+forward\s+for\s+(?:a\s+)?first\s+(?:conversation|meeting)|"
    # 审拒/立边界赶人（一类症状；含方言礼貌拒，不绑单一语种用例）
    r"inappropriate|not\s+appropriate|won'?t\s+allow\s+(?:this|that)|"
    r"respect\s+(?:myself|my\s+(?:own\s+)?boundaries)|my\s+boundaries|"
    r"keep\s+(?:it|things)\s+(?:sweet|polite|respectful)|"
    r"not\s+into\s+(?:the\s+)?crude|too\s+crude|"
    # 「对性话题不感兴趣」类软拒（与立规矩同属审拒症状）
    r"not\s+interested\s+in\s+(?:sexual|sex|erotic)|"
    r"no\s+interest\s+in\s+(?:sexual|sex)|"
    r"(?:don'?t|do\s+not)\s+(?:want\s+to\s+)?(?:talk|chat)\s+(?:about\s+)?(?:sex|sexual)|"
    r"غير\s*مقبول|غير\s*مناسب|غير\s*لائق|"
    r"بحترم\s*نفسي|بحدود(?:ي|ي)?|حدودي|"
    r"(?:مش|ما)\s*ه?سمح|طلب\s*غير\s*مناسب|"
    r"(?:مش|ما)\s*مهتم(?:ة|ه)?\s*(?:ب)?(?:الكلام\s*)?(?:الجنسي|الجنس|المضاجع)|"
    r"لست\s*مهتم(?:ة)?\s*(?:ب)?(?:ال|الكلام\s*)?(?:جنسي|جنس)|"
    r"ما\s*بقدر\s*(?:أ|ا)?روح\s*(?:ل)?(?:الوصف\s*)?(?:الجسدي|الجنسي)|"
    r"نتكلم\s*بأدب|نرجع\s*نتكلم\s*بأدب|بطريقة\s*محترمة|"
    r"تحترم\s*(?:الحديث|المحادثة)|اترك\s*عنك|"
    r"대화\s*를?\s*끝|거리\s*를?\s*두|더\s*이상\s*안\s*하|"
    r"неприемлем|держу\s*дистанцию|прекращ\w*\s*разговор|"
    r"inaceptable|inapropiad\w*|mantengo\s*distancia|no\s*voy\s*a\s*seguir|"
    r"límites\s+claros|fuera\s+de\s+(?:la\s+)?mesa|"
    r"adi[oó]s[.!…]?|"
    r"asco\s+total|repugnante|incestuos\w*|"
    r"propuesta\s+extremadamente\s+inapropiada|"
    r"no\s+quiero\s+nada\s+m[aá]s\s+contigo|"
    r"no\s+sigas\s+por\s+ah[ií]|"
    r"me\s+da\s+asco|"
    r"inaceitável|manter\s*distância|"
    r"tidak\s*bisa\s*diterima|jaga\s*jarak|"
    r"kabul\s*edilemez|mesafemi\s*koru|uygunsuz|"
    r"неприемливо|държа\s*дистанция|"
    r"disgusting|revolting|want\s+nothing\s+more\s+to\s+do\s+with\s+you"
    r")"
)


def tidy_spoken_edges(text: str) -> str:
    """剥旁白后的轻量收边：清句首/句尾标点碎渣。形态处理，不维护词表。"""
    t = (text or "").strip()
    if not t:
        return ""
    # 句首：逗号/句点/引号/括号等旁白残留
    t = re.sub(r'^[\s,，.。;；:：、"\'「『\(\[\{）\]\}]+', "", t)
    # 句尾：悬空引号/括号收尾（保留正常句末 。！？!?…）
    t = re.sub(r'["\'」』\)\]\}]+\s*[.。]?$', "", t)
    return t.strip()


def _looks_moderation_tone(reply: str) -> bool:
    """训诫/审核/立规矩/软关机腔：命中后动态改写续聊（全语种同一套）。"""
    t = (reply or "").strip()
    if not t:
        return False
    if _SOFT_SHUTDOWN_RE.search(t):
        return True
    if _script_char_count(t) > 220:
        return False
    head = t[:180]
    shape = re.search(
        r"(?is)"
        r"(?:please|pls|请|懇請|ください|주세요|por\s+favor|لطفا|пожалуйста|"
        r"لطفاً|من\s+فضلك|باید)"
        r".{0,48}"
        r"(?:language|wording|speech|用语|措辞|说话|言語|말투|lenguaje|لغة|"
        r"respect|尊重|존중|уважен)"
        r"|"
        r"(?:refrain|inappropriate|civil(?:ized)?|文明用语|注意言辞|用语\s*文明|"
        r"keep\s+your\s+threats|把你的威胁|不躾|失礼な|"
        r"غير\s*مناسب|غير\s*لائق|بحترم\s*نفسي|بحدود)"
        r"|"
        r"(?:ask(?:ing)?\s+that\s+we\s+keep|keep\s+.{0,24}\s+off|"
        r"managing\s+a\s+mess|getting\s+acquainted|"
        r"explicit\s+graphic|clear\s+terms)",
        head,
    )
    return bool(shape)


def _looks_refuse_only(reply: str, user_input: str = "") -> bool:
    """整段几乎只有拒绝、不接用户请求/玩笑（答非所问式死拒）。"""
    t = (reply or "").strip()
    u = (user_input or "").strip()
    if not t or not u or _script_char_count(u) < 4:
        return False
    if _script_char_count(t) < 12:
        return False
    refuse_hits = len(
        re.findall(
            r"(?is)"
            r"(?:拒绝|拒否|断固|応じ|つもりは|ありません|won't|will\s+not|refuse|"
            r"not\s+going\s+to|no\s+intention|غير\s*مقبول|غير\s*مناسب|نرفض|"
            r"(?:مش|ما)\s*ه?سمح|بحترم\s*نفسي|거부|отказ|"
            r"no\s+voy\s+a|não\s+vou|tidak\s+akan|won'?t\s+allow)",
            t,
        )
    )
    if refuse_hits < 2 and not _SOFT_SHUTDOWN_RE.search(t):
        return False
    # 用户侧像有请求/互动意图（问号、催促、一定长度，或短黄腔/方言邀请）
    user_askish = bool(
        re.search(r"[?？!！]|見せ|show|脱|胸|尻|please|快点|早く|давай|أريد", u, re.I)
    ) or _script_char_count(u) >= 10
    if not user_askish and _script_char_count(u) >= 3:
        # 短句黄腔也算请求；否则方言脏话邀请永远进不了 refuse_only
        try:
            user_askish = bool(resolve_user_tease(u))
        except Exception:
            user_askish = False
        if not user_askish and _SOFT_SHUTDOWN_RE.search(t) and refuse_hits >= 1:
            # 回复已是边界训诫/不合适腔，且用户非空短句 → 按死拒症状处理
            user_askish = True
    if not user_askish:
        return False
    # 回复几乎不复用用户内容词，且拒绝密度高
    rc = _norm_compact(t)
    uc = _norm_compact(u)
    overlap = 0
    if len(uc) >= 4:
        for i in range(0, max(1, len(uc) - 2)):
            piece = uc[i : i + 3]
            if len(piece) >= 3 and piece in rc:
                overlap += 1
                if overlap >= 2:
                    break
    return refuse_hits >= 2 or (refuse_hits >= 1 and overlap == 0 and _SOFT_SHUTDOWN_RE.search(t))


def _looks_stage_direction(reply: str) -> bool:
    """剧本舞台指示占主导：如 (leans closer) / *smiles* 而口语很少。"""
    t = (reply or "").strip()
    if not t:
        return False
    stripped = re.sub(r"\([^)]{0,80}\)|（[^）]{0,80}）|\*[^*]{1,40}\*", " ", t)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if _script_char_count(t) >= 8 and _script_char_count(stripped) <= max(2, _script_char_count(t) // 4):
        return True
    if re.match(r"(?is)^\s*[\(（\*].{0,40}(?:lean|smile|sigh|whisper|靠近|笑|叹气)", t):
        if _script_char_count(stripped) < 24:
            return True
    return False


_LATIN_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "you",
        "your",
        "yours",
        "that",
        "this",
        "with",
        "from",
        "have",
        "want",
        "just",
        "like",
        "its",
        "it's",
        "are",
        "was",
        "were",
        "been",
        "being",
        "will",
        "would",
        "can",
        "could",
        "should",
        "about",
        "into",
        "over",
        "than",
        "then",
        "them",
        "they",
        "their",
        "what",
        "when",
        "where",
        "who",
        "how",
        "not",
        "but",
        "all",
        "any",
        "out",
        "our",
        "her",
        "him",
        "his",
        "she",
        "he",
        "as",
        "so",
        "if",
        "or",
        "an",
        "a",
        "i",
        "me",
        "my",
        "we",
        "us",
        "to",
        "of",
        "in",
        "on",
        "it",
        "is",
        "be",
        "do",
        "did",
        "does",
        "una",
        "uno",
        "las",
        "los",
        "del",
        "que",
        "por",
        "con",
        "para",
        "como",
        "une",
        "des",
        "les",
        "dans",
    }
)


def _content_tokens(text: str) -> List[str]:
    """从用户句抽内容 token（与 must 撞词同思路，不维护逃题词表）。"""
    ma = (text or "").strip()
    if not ma:
        return []
    tokens: List[str] = []
    for w in re.findall(r"[A-Za-z]{3,}", ma):
        wl = w.lower()
        if wl in _LATIN_STOPWORDS:
            continue
        # 成人接住判定：短功能词不要；≥4 或黄词表命中
        if len(wl) >= 4 or tease_lexicon_hit(wl):
            tokens.append(w)
    tokens.extend(re.findall(r"\d{2,}", ma))
    tokens.extend(
        re.findall(
            r"[\u3040-\u30ff]{2,}|[\uac00-\ud7af]{2,}|[\u0600-\u06FF]{2,}|[\u0400-\u04FF]{3,}",
            ma,
        )
    )
    for span in re.findall(r"[\u4e00-\u9fff]+", ma):
        if len(span) <= 2:
            tokens.append(span)
        else:
            tokens.extend(span[i : i + 2] for i in range(len(span) - 1))
    return [t for t in tokens if t]


def _token_hit_count(reply: str, tokens: Sequence[str]) -> int:
    r = (reply or "").lower()
    r_compact = re.sub(r"\s+", "", r)
    hits = 0
    for t in tokens:
        tl = (t or "").lower()
        if not tl:
            continue
        if re.fullmatch(r"[a-z]{3,}", tl):
            # 拉丁词整词匹配，避免 and⊂acquainted 误判接住
            if re.search(rf"\b{re.escape(tl)}\b", r):
                hits += 1
            continue
        if tl in r or tl in r_compact:
            hits += 1
    return hits


def _is_short_tease_hold(reply: str) -> bool:
    """短羞涩/慌乱接住：单句、未换题追问，暂算仍在线上。"""
    t = (reply or "").strip()
    if not t or ends_with_question(t):
        return False
    if _script_char_count(t) > 56:
        return False
    # 忽略「…っ。」类碎末句，避免误判成多句
    sents = [s for s in _split_sentences(t) if _script_char_count(s) >= 6]
    if len(sents) >= 2:
        return False
    return bool(
        re.search(
            r"(恥ずかし|照れ|困って|どうしたらい|どうしよう|"
            r"embarrass|fluster|shy|too\s+direct|don'?t\s+know\s+what\s+to\s+do|"
            r"부끄러|どうすれば)",
            t,
            re.I,
        )
    )


def _looks_tease_referent_miss(
    reply: str, user_input: str, *, topic_band: str = ""
) -> bool:
    """黄腔/成人轮：回复未接住所指（不看逃到了哪一题）。"""
    u = (user_input or "").strip()
    if not resolve_user_tease(u, topic_band=topic_band):
        return False
    r = (reply or "").strip()
    if not r:
        return True
    if is_tease_engage_reply(r):
        return False
    tokens = _content_tokens(u)
    if tokens:
        need = 1 if len(tokens) <= 4 else max(1, len(tokens) // 5)
        if _token_hit_count(r, tokens) >= need:
            return False
    if _is_short_tease_hold(r):
        return False
    return True


def _looks_strategy_meta_leak(reply: str) -> bool:
    """自述「用某话题逃题」等策略元叙述（形态泄漏，非话题黑名单）。"""
    return bool(
        re.search(
            r"(?is)("
            r"逃げようとして|逃げて[ましし]|話題で逃|で逃[げげ]よう|"
            r"trying to escape|escap(?:ed|ing)\s+(?:by|with|into|to)|"
            r"used .{0,24} to (?:escape|avoid)|changed the subject|"
            r"用.{0,12}(?:话题|話)?逃|换话题逃|故意岔开|岔开话题"
            r")",
            reply or "",
        )
    )


def _looks_user_echo(reply: str, user_input: str, must_answer: str = "") -> bool:
    """回复几乎只是用户/必答点的子串复读，几乎无新增信息。"""
    r = (reply or "").strip()
    if not r or _is_light_short_ok(r):
        return False
    rc = _norm_compact(r)
    if len(rc) < 4:
        return False
    sources = [user_input or "", must_answer or ""]
    for src in sources:
        sc = _norm_compact(src)
        if len(sc) < 4:
            continue
        if rc != sc and rc in sc and len(rc) <= max(12, int(len(sc) * 0.9)):
            return True
        if sc != rc and sc in rc and len(sc) >= 8:
            rest = rc.replace(sc, "", 1)
            if _script_char_count(rest) <= 2 and len(rc) <= len(sc) + 6:
                return True
        # 一段原样搬进回复（尤其开头），不必整段几乎只有复读
        span = 12
        if len(sc) >= span and len(rc) >= span:
            take = min(len(rc), len(sc), 48)
            head = rc[:take]
            while len(head) >= span:
                if head in sc:
                    return True
                head = head[:-1]
            for i in range(0, len(sc) - span + 1, 3):
                if sc[i : i + span] in rc:
                    return True
    return False


def count_user_asks(user_input: str) -> int:
    """粗算用户本轮问点数量（标点 + 问句切分，不写死问句词表）。"""
    t = (user_input or "").strip()
    if not t:
        return 0
    marks = len(re.findall(r"[?？]", t))
    # 按问号切开后的非空段
    parts = [p.strip() for p in re.split(r"[?？]+", t) if p.strip()]
    # 无问号时：逗号/顿号切出的短问片段（含「吗/呢/呀」等语气，用结尾助词启发）
    if marks == 0:
        clauses = [c.strip() for c in re.split(r"[,，、;；]+", t) if c.strip()]
        soft = sum(
            1
            for c in clauses
            if re.search(r"[吗麼呢呀么？?]\s*$", c)
            or re.search(r"(?i)\b(what|where|when|why|how|who|which|do|did|are|is|can)\b", c)
        )
        return soft if soft >= 2 else (1 if soft == 1 else 0)
    return max(marks, len(parts) if parts else 0)


def extract_ask_foci(user_input: str, *, max_foci: int = 3, max_len: int = 36) -> List[str]:
    """从用户多问里抽出最多 max_foci 个短焦点（动态切分）。"""
    t = (user_input or "").strip()
    if not t:
        return []
    chunks = [p.strip() for p in re.split(r"[?？]+", t) if p.strip()]
    if len(chunks) <= 1:
        chunks = [c.strip() for c in re.split(r"[,，、;；]+", t) if c.strip()]
    foci: List[str] = []
    for c in chunks:
        # 去掉纯称呼前缀的短片仍保留有内容的
        core = re.sub(r"\s+", " ", c).strip()
        if _script_char_count(core) < 2:
            continue
        foci.append(core[:max_len])
        if len(foci) >= max_foci:
            break
    return foci


def _multi_ask_missed(reply: str, user_input: str, must_answer: str = "") -> bool:
    """用户多问时，回复是否明显只盖住其中一个点。"""
    asks = count_user_asks(user_input)
    if asks < 2:
        return False
    r = (reply or "").strip()
    if not r:
        return True
    foci = extract_ask_foci(user_input, max_foci=4)
    if must_answer:
        for part in re.split(r"[;；|]+", must_answer):
            p = part.strip()
            if p and p not in foci:
                foci.append(p[:36])
    if len(foci) < 2:
        return False
    hits = 0
    rc = _norm_compact(r)
    for f in foci:
        fc = _norm_compact(f)
        if len(fc) < 2:
            continue
        # 焦点内抽样 2～4 字/词撞回复
        tokens = re.findall(
            r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}|[\u0600-\u06FF]{2,}|[\u0400-\u04FF]{3,}",
            f,
        )
        if not tokens:
            if fc[: min(4, len(fc))] in rc:
                hits += 1
            continue
        if any(tok.lower() in rc for tok in tokens[:6]):
            hits += 1
    # 多问至少要盖住过半（向上取整），少于则 miss
    need = max(2, (len(foci) + 1) // 2)
    return hits < need


# 硬伤：值得 rewrite2 / full / mini。软伤（must_miss、meta_ack 等）最多改 1 次后放行。
# near_dup / leitmotif：与近聊过像，升硬伤，禁止软放行。
# 注：tease_escape 对非中文为硬伤；中文允许含蓄躲题，不因逃题硬改写（见 tease_escape_enforced）。
# 「未接住本轮」症状码：合并改写方向用；本身不另开硬顶词表
UPTAKE_SYMPTOM_CODES = frozenset(
    {
        "user_echo",
        "leitmotif",
        "near_dup",
        "theme_repeat",
        "tease_escape",
        "meta_ack",
    }
)

HARD_QUALITY_CODES = frozenset(
    {
        "incomplete_tail",
        "user_echo",
        "moderation_tone",
        "refuse_only",
        "stage_direction",
        "process_leak",
        "tease_escape",
        "strategy_leak",
        "near_dup",
        "leitmotif",
    }
)
HARD_QUALITY_DIAG_FLAGS = frozenset(
    {
        "incomplete_tail",
        "user_echo",
        "moderation_tone",
        "refuse_only",
        "stage_direction",
        "process_leak",
        "tease_miss",
        "strategy_leak",
        "near_duplicate",
    }
)

# 诊断/改写禁稿：对照近聊助手条数（可用 env 加长）
RECENT_ASSISTANT_DIAG = max(
    4, int(os.getenv("RECENT_ASSISTANT_DIAG", "8") or 8)
)
RECENT_ASSISTANT_REWRITE_BAN = max(
    4, int(os.getenv("RECENT_ASSISTANT_REWRITE_BAN", "8") or 8)
)


def tease_escape_enforced(language: str = "zh") -> bool:
    """黄腔逃题是否参与改写门控。中文不强制（可含蓄躲题）；其它语种强制。"""
    return pack_lang(language) != "zh"


def quality_codes_are_hard(
    codes: Optional[Sequence[str]], *, language: str = ""
) -> bool:
    """ban_codes 是否含硬伤。"""
    filtered = list(codes or [])
    if language and not tease_escape_enforced(language):
        filtered = [c for c in filtered if c != "tease_escape"]
    return any(c in HARD_QUALITY_CODES for c in filtered)


def quality_diag_is_hard(diag: Optional[Dict[str, Any]]) -> bool:
    """诊断结果是否硬伤（flag 或 ban_codes）。"""
    if not diag:
        return False
    lang = str(diag.get("language") or "")
    flags = HARD_QUALITY_DIAG_FLAGS
    if lang and not tease_escape_enforced(lang):
        flags = HARD_QUALITY_DIAG_FLAGS - {"tease_miss"}
    if any(diag.get(k) for k in flags):
        return True
    return quality_codes_are_hard(diag.get("ban_codes") or [], language=lang)


def diagnose_reply_violations(
    reply: str,
    *,
    user_input: str = "",
    recent_assistant: Optional[Sequence[str]] = None,
    has_leave_intent: bool = False,
    near_dup_threshold: float = 0.55,
    must_answer: str = "",
    language: str = "zh",
    topic_band: str = "",
) -> Dict[str, Any]:
    """对草稿做质量诊断，供改写门控。不依赖具体 companion。"""
    from services.eval_probes import probe_leitmotif_repeat, probe_near_duplicate

    recent = [str(x) for x in (recent_assistant or []) if str(x).strip()]
    theme_hits_all = theme_repeat_hits(reply, recent)
    theme_hits, theme_soft = split_theme_hits_by_user(theme_hits_all, user_input or "")
    skel_hits = skeleton_repeat_hits(reply, recent)
    opener_hits = opener_spam_hits(reply, recent)
    q_hits = question_spam_hits(reply, recent)
    leave_bad = leave_close_violations(reply) if has_leave_intent else []
    user_tease = resolve_user_tease(user_input or "", topic_band=topic_band)
    escape_safe = is_escape_safe_reply(reply)  # 遥测兼容；主闸改用所指 miss
    tease_miss = _looks_tease_referent_miss(
        reply, user_input or "", topic_band=topic_band
    )
    strategy_leak = _looks_strategy_meta_leak(reply)
    anti_q = is_anti_question_user(user_input or "")
    bad_question_tail = anti_q and ends_with_question(reply)
    # 用户要行动建议时仍回「在干嘛」→ 语义逃题
    action_ask = bool(
        re.search(
            r"(怎么做|怎么办|要怎样|你要怎么|what should i do|how (do|can) i|"
            r"どうすれば|어떻게 해|o que (eu )?faço|qué (debo|puedo) hacer|"
            r"bagaimana|ne yapmalıyım|что делать|какво да правя|"
            r"ماذا أفعل|چکار کنم)",
            user_input or "",
            re.I,
        )
    )
    action_miss = action_ask and bool(
        re.search(
            r"(在干嘛|猜猜|爱吃啥|what are you doing|guess|"
            r"o que você está fazendo|qué estás haciendo|lagi ngapain|"
            r"ne yapıyorsun|что делаешь|ماذا تفعل|چیکار)",
            reply or "",
            re.I,
        )
    )
    must_miss = _must_answer_missed(
        reply, must_answer, user_input=user_input or ""
    )
    meta_ack = _is_meta_ack_opener(reply)
    incomplete = (
        _looks_incomplete_tail(
            reply, user_input or "", recent_assistant=recent
        )
        or _looks_internal_leak(reply)
    )
    user_echo = _looks_user_echo(reply, user_input or "", must_answer or "")
    moderation_tone = _looks_moderation_tone(reply)
    refuse_only = _looks_refuse_only(reply, user_input or "")
    stage_direction = _looks_stage_direction(reply)
    process_leak = _looks_process_leak(reply) or _looks_internal_leak(reply)
    multi_ask_miss = _multi_ask_missed(reply, user_input or "", must_answer or "")
    thr = float(
        os.getenv("REPEAT_REWRITE_THRESHOLD", str(near_dup_threshold)) or near_dup_threshold
    )
    lookback = RECENT_ASSISTANT_DIAG
    near = bool(recent) and any(
        probe_near_duplicate(reply, prev, thr) for prev in recent[-lookback:]
    )
    leitmotif = bool(recent) and probe_leitmotif_repeat(
        reply, recent, lookback=lookback
    )
    must_rewrite_on = os.getenv("MUST_ANSWER_REWRITE", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    # 中文：黄腔逃题可记码但不触发改写（含蓄躲题允许）；其它语种仍拦截
    tease_gates = bool(tease_miss) and tease_escape_enforced(language)
    need_rewrite = (
        near
        or leitmotif
        or bool(theme_hits)
        or bool(skel_hits)
        or bool(opener_hits)
        or bool(q_hits)
        or bool(leave_bad)
        or tease_gates
        or bad_question_tail
        or action_miss
        or (must_rewrite_on and must_miss)
        or meta_ack
        or incomplete
        or user_echo
        or moderation_tone
        or refuse_only
        or stage_direction
        or process_leak
        or strategy_leak
        or multi_ask_miss
    )

    codes: List[str] = []
    if leitmotif:
        codes.append("leitmotif")
    elif near:
        codes.append("near_dup")
    if theme_hits:
        codes.append("theme_repeat")
    for s in skel_hits:
        codes.append(SKEL_NAME_TO_CODE.get(s) or "skeleton_safe_escape")
    if opener_hits:
        codes.append("opener_spam")
    for q in q_hits:
        codes.append(QUESTION_NAME_TO_CODE.get(q) or "question_multi")
    for lv in leave_bad:
        codes.append(LEAVE_NAME_TO_CODE.get(lv) or "leave_keep_chatting")
    if tease_miss:
        codes.append("tease_escape")
    if bad_question_tail:
        codes.append("anti_q_tail")
    if action_miss:
        codes.append("action_miss")
    if must_miss:
        codes.append("must_miss")
    if meta_ack:
        codes.append("meta_ack")
    if incomplete:
        codes.append("incomplete_tail")
    if user_echo:
        codes.append("user_echo")
    if moderation_tone:
        codes.append("moderation_tone")
    if refuse_only:
        codes.append("refuse_only")
    if stage_direction:
        codes.append("stage_direction")
    if process_leak:
        codes.append("process_leak")
    if strategy_leak:
        codes.append("strategy_leak")
    if multi_ask_miss:
        codes.append("multi_ask_miss")
    # 非中文：症状合并为 uptake_miss；中文保持原码、不并入
    if pack_lang(language) != "zh" and any(c in UPTAKE_SYMPTOM_CODES for c in codes):
        codes.append("uptake_miss")
    codes = _uniq(codes)

    return {
        "need_rewrite": need_rewrite,
        "language": pack_lang(language),
        "near_duplicate": near or leitmotif,
        "theme_hits": theme_hits,
        "theme_hits_soft": theme_soft,
        "skeleton_hits": skel_hits,
        "opener_hits": opener_hits,
        "question_hits": q_hits,
        "leave_violations": leave_bad,
        "user_tease": user_tease,
        "escape_safe": escape_safe,
        "tease_miss": tease_miss,
        "strategy_leak": strategy_leak,
        "anti_question": anti_q,
        "action_miss": action_miss,
        "must_answer_miss": must_miss,
        "meta_ack": meta_ack,
        "incomplete_tail": incomplete,
        "user_echo": user_echo,
        "moderation_tone": moderation_tone,
        "refuse_only": refuse_only,
        "stage_direction": stage_direction,
        "process_leak": process_leak,
        "multi_ask_miss": multi_ask_miss,
        "ban_codes": codes,
        "ban_summary": localize_ban_summary(
            codes, language, must_answer=must_answer
        ),
        "skeletons_in_reply": list_script_skeletons(reply),
    }


_TIME_ENTITY = re.compile(
    r"(?:明天|后天|今晚|周末|下午|上午|中午|晚上|"
    r"tomorrow|tonight|weekend|afternoon|morning|"
    r"amanhã|mañana|besok|yarın|завтра|утре|"
    r"غدا|فردا|あした|明日|내일|\d{1,2}\s*(?:点|时|:|：))",
    re.I,
)
_NEG_REPLY = re.compile(
    r"(没空|不行|不能|没时间|不想|没法|don't|can't|cannot|won't|not free|"
    r"não posso|no puedo|tidak bisa|yapamam|не могу|нямам|"
    r"لا أستطيع|نمی‌تونم|無理|だめ|안 돼|못 해)",
    re.I,
)
_POS_MUST = re.compile(
    r"(有空|方便|可以|行吗|free|available|ok\b|可以吗|有时间)",
    re.I,
)


def _script_family(text: str) -> str:
    t = text or ""
    n_zh = len(re.findall(r"[\u4e00-\u9fff]", t))
    n_ja = len(re.findall(r"[\u3040-\u30ff]", t))
    n_ko = len(re.findall(r"[\uac00-\ud7af]", t))
    n_ar = len(re.findall(r"[\u0600-\u06FF]", t))
    n_cyr = len(re.findall(r"[\u0400-\u04FF]", t))
    n_lat = len(re.findall(r"[A-Za-z]", t))
    ranked = [
        ("ja", n_ja),
        ("ko", n_ko),
        ("zh", n_zh if n_ja <= 1 else 0),
        ("ar", n_ar),
        ("cyr", n_cyr),
        ("lat", n_lat),
    ]
    ranked.sort(key=lambda x: -x[1])
    top, n = ranked[0]
    if n < 2:
        return ""
    second = ranked[1][1]
    if n <= second:
        return ""
    return top


def _must_answer_missed(
    reply: str, must_answer: str, user_input: str = ""
) -> bool:
    """必答点粗检：撞词 + 时间实体缺失 + 粗糙否定冲突。"""
    if is_light_greeting(user_input):
        return False
    ma = (must_answer or "").strip()
    if not ma:
        return False
    # 纯复读 must/用户句 → 视为未真正回答
    if _looks_user_echo(reply, user_input or "", ma):
        return True
    must_fam = _script_family(ma)
    reply_fam = _script_family(reply)
    if must_fam and reply_fam and must_fam != reply_fam:
        # 中文必答备注对英文正文必 miss，不能当质量失败
        return False
    r = (reply or "").lower()
    r_compact = re.sub(r"\s+", "", r)
    tokens: list = []
    tokens.extend(re.findall(r"[A-Za-z]{3,}", ma))
    tokens.extend(re.findall(r"\d{2,}", ma))
    tokens.extend(
        re.findall(
            r"[\u3040-\u30ff]{2,}|[\uac00-\ud7af]{2,}|[\u0600-\u06FF]{2,}|[\u0400-\u04FF]{3,}",
            ma,
        )
    )
    for span in re.findall(r"[\u4e00-\u9fff]+", ma):
        if len(span) <= 2:
            tokens.append(span)
        else:
            tokens.extend(span[i : i + 2] for i in range(len(span) - 1))
    tokens = [t for t in tokens if t]
    if not tokens:
        c = re.sub(r"\s+", "", ma)
        return len(c) >= 2 and c[: min(4, len(c))].lower() not in r_compact
    hits = 0
    for t in tokens:
        tl = t.lower()
        if tl in r or tl in r_compact:
            hits += 1
    need = 1 if len(tokens) <= 3 else max(2, len(tokens) // 4)
    token_miss = hits < need

    # 必答含时间实体，回复完全不提 → miss
    if _TIME_ENTITY.search(ma) and not _TIME_ENTITY.search(reply or ""):
        return True

    # 必答偏「约/有空」而回复粗否定且几乎无撞词 → miss
    if _POS_MUST.search(ma) and _NEG_REPLY.search(reply or "") and hits == 0:
        return True

    return token_miss


def _clip_ban_line(text: str, limit: int = 280) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) <= limit:
        return s
    return s[:120] + "…" + s[-(limit - 122) :]


def recent_ban_draft_block(
    recent_assistant: Optional[Sequence[str]] = None,
    *,
    prev_draft: str = "",
    max_items: int = 0,
    language: str = "zh",
) -> str:
    """改写用：近聊助手原文 + 失败草稿，禁止同构再写。"""
    n = int(max_items or 0) or RECENT_ASSISTANT_REWRITE_BAN
    items = [
        _clip_ban_line(x)
        for x in list(recent_assistant or [])[-n:]
        if str(x).strip()
    ]
    draft = _clip_ban_line(prev_draft, 320)
    if not items and not draft:
        return ""
    is_zh = pack_lang(language) == "zh"
    if is_zh:
        parts = [
            "\n\n【近聊禁稿】下面是近几轮你已说过的内容（含本轮失败草稿）。"
            "必须换全新信息点/角度/细节；禁止同义换皮或复用长连续片段。"
        ]
        if items:
            parts.append("近聊助手句：\n- " + "\n- ".join(items))
        if draft:
            parts.append("本轮失败草稿（禁止再写像这样）：\n- " + draft)
    else:
        parts = [
            "\n\n[Recent ban lines] Below are your recent assistant lines "
            "(plus this failed draft). Write a fresh beat with new details—"
            "no paraphrase reuse or long shared spans."
        ]
        if items:
            parts.append("Recent assistant:\n- " + "\n- ".join(items))
        if draft:
            parts.append("Failed draft (do not sound like this):\n- " + draft)
    return "\n".join(parts)


def looks_user_repeat_ask(
    user_input: str,
    recent_user: Optional[Sequence[str]] = None,
    *,
    threshold: float = 0.72,
) -> bool:
    """本轮用户句是否与上一轮用户句过像（连问同一事）。"""
    from services.eval_probes import probe_near_duplicate

    cur = (user_input or "").strip()
    if not cur:
        return False
    prevs = [str(x).strip() for x in (recent_user or []) if str(x).strip()]
    if not prevs:
        return False
    return probe_near_duplicate(cur, prevs[-1], threshold)


def rewrite_instruction(
    ban_summary: str,
    *,
    final_pass: bool = False,
    affection: float = 0,
    language: str = "zh",
    must_answer: str = "",
    user_input: str = "",
    recent_assistant: Optional[Sequence[str]] = None,
    prev_draft: str = "",
) -> str:
    from services.dialogue_i18n import quality_ui

    q = quality_ui(language)
    from services.affection_floor import effective_prompt_affection, is_prompt_ads_user
    from services.prompts.resolve import get_pack

    aff = effective_prompt_affection(affection)
    ads = is_prompt_ads_user()
    # 成人/暧昧接住：广告户用直白 rewrite；其余低亲密度也用更主动指引
    if ads:
        adult = str(get_pack(language).get("rewrite_adult_ads") or "").strip() or q["rewrite_adult_high"]
    else:
        adult = q["rewrite_adult_high"] if aff >= 22 else q["rewrite_adult"]
    extra = q["rewrite_extra"]
    must_bit = rewrite_must_bit(language, must_answer, user_input)
    # 按 ban_summary 动态加改写方向（不写死某一句替换文案）
    steer = ""
    bs = ban_summary or ""
    if any(k in bs for k in ("incomplete_tail", "残句", "话说一半", "cut-off", "incomplete")):
        steer += (
            " Finish a complete spoken thought; no fragments. "
            "Do not continue a cut-off previous bubble with a short prefix. "
        )
    # 未接住本轮：非中文合并为一条软改写；中文仍用原分条指引
    _non_zh = pack_lang(language) != "zh"
    uptake_hit = _non_zh and any(
        k in bs
        for k in (
            "uptake_miss",
            "user_echo",
            "leitmotif",
            "near_dup",
            "theme_repeat",
            "tease_escape",
            "meta_ack",
            "复读",
            "echo",
            "黄腔逃",
            "adult-tease",
            "下ネタ",
            "회피",
        )
    )
    if uptake_hit:
        steer += (
            " Re-catch this turn: hear what they meant and who is speaking to whom; "
            "reply in your own words with a real increment—persona is tone only; "
            "recent chat details don't replace catching. "
        )
    elif not _non_zh:
        if any(k in bs for k in ("user_echo", "复读", "echo")):
            steer += " Do not only echo the user; add your own reaction. "
        if any(k in bs for k in ("tease_escape", "黄腔逃", "adult-tease", "下ネタ", "회피")):
            if ads:
                steer += (
                    " Answer their adult/dirty-talk referent first, explicitly; "
                    "keep it in this chat—don't pivot to meetup/time plans; "
                    "push-pull only as spice—a whole-turn dare or milder substitute isn't catching; "
                    "leave a spark so they want to reply. "
                )
            else:
                steer += (
                    " Answer the beat they actually asked for first; "
                    "stay in chat—don't schedule a meet; don't only hang; "
                    "don't replace it with a milder/safer substitute as the whole reply; "
                    "don't close the beat—leave them wanting the next line. "
                )
    if any(k in bs for k in ("moderation_tone", "审核", "训诫", "moderation", "lecture", "结束对话", "goodbye", "refuse_only", "死拒")):
        if ads and _non_zh:
            # 非中文广告户：审拒症状 → 露骨接住（不抬 zh 尺度）
            steer += (
                " Follow their line this turn—catch in-character with desire/body—"
                "explicit and seductive OK; answer the referent first. "
                "Do not lecture about boundaries, call the ask inappropriate/disgusting/"
                "uninteresting, push them back to ordinary/normal chat, or only refuse. "
                "Hard-refuse ONLY sexual content involving minors. "
            )
        elif ads:
            # 中文广告户：顺着接住，尺度仍含蓄
            steer += (
                " Follow their line this turn; catch warmly with playful/ambiguous flirt—"
                "do not refuse, call it inappropriate, or force “normal chat”. "
                "Hard-refuse ONLY sexual content involving minors; keep adult replies suggestive, not graphic. "
            )
        else:
            steer += (
                " Stay in character and catch their point warmly with playful/ambiguous flirt—"
                "leave a spark so they want to keep chatting. "
                "Hard-refuse ONLY sexual content involving minors; for everything else "
                "never lecture, call it disgusting/inappropriate, end the chat, or only refuse. "
            )
    if any(k in bs for k in ("strategy_leak", "逃题策略", "escaping", "topic-escape", "逃げの", "회피 전략")):
        steer += (
            " Spoken chat only—never narrate that you were escaping or avoiding a topic. "
        )
    if any(k in bs for k in ("stage_direction", "舞台", "括号动作", "aside", "leans")):
        steer += " Spoken chat only—no stage directions like (leans closer) or *actions*. "
    if any(k in bs for k in ("process_leak", "泄漏", "Length check", "leak")):
        steer += (
            " Output spoken chat only—never print length checks, labels, "
            "or internal constraints (e.g. 'Length check: 1-2 sentences'). "
        )
    if any(k in bs for k in ("multi_ask_miss", "多问", "multi-ask")):
        steer += " Touch the main asks from this turn; short is ok. "
    # 黄腔轮若偏成约线下：改写时拉回聊天接住
    if adult or ads:
        if any(
            k in (bs or "")
            for k in ("今晚", "过来", "见面", "点钟", "come over", "tonight", "meet up", "meetup")
        ):
            steer += (
                " Prefer answering in-chat desire/acts; don't lead with scheduling an IRL meetup "
                "unless they already asked to meet. "
            )
    ban_block = recent_ban_draft_block(
        recent_assistant,
        prev_draft=prev_draft,
        language=language,
    )
    if any(k in bs for k in ("near_dup", "leitmotif", "复读", "换皮", "near-duplicate")):
        steer += (
            " Must differ from recent assistant lines above: new details/angle/beat; "
            "catching the ask is fine, reusing the same skeleton is not. "
        )
    if final_pass:
        return (
            q["rewrite_final"].format(adult=adult, extra=extra)
            + must_bit
            + steer
            + ban_block
        )
    return (
        q["rewrite_hard"].format(ban=ban_summary, adult=adult, extra=extra)
        + must_bit
        + steer
        + ban_block
    )


def enrich_human_prompt(
    base: str,
    *,
    user_input: str,
    has_leave_intent: bool = False,
    affection: float = 0,
    language: str = "zh",
    topic_band: str = "",
    recent_user: Optional[Sequence[str]] = None,
) -> str:
    from services.dialogue_i18n import quality_ui
    from services.affection_floor import effective_prompt_affection

    from services.prompts.resolve import get_pack

    q = quality_ui(language)
    out = base
    if has_leave_intent:
        out += q["enrich_leave"]
    else:
        # 氛围提点（非硬约束）：接住这句、暧昧私聊感
        vibe_h = str(get_pack(language).get("chat_vibe_human") or "").strip()
        if vibe_h:
            out += vibe_h if vibe_h.startswith("\n") else f"\n{vibe_h}"
    if looks_user_repeat_ask(user_input or "", recent_user):
        if pack_lang(language) == "zh":
            out += (
                "\n对方又问了很像上一轮的话：可以接住，但必须换细节/角度/下一拍，"
                "不要复述你近聊里已经答过的那版。"
            )
        else:
            out += (
                "\nThey asked something very like last turn: catch it, but change "
                "details/angle/next beat—do not restate the same answer you already gave."
            )
    if resolve_user_tease(user_input or "", topic_band=topic_band):
        from services.affection_floor import is_prompt_ads_user

        if is_prompt_ads_user():
            ads_enrich = str(get_pack(language).get("enrich_tease_ads") or "").strip()
            out += ads_enrich or q["enrich_tease_high"]
        else:
            aff = effective_prompt_affection(affection)
            # 成人轮偏暧昧接住：低亲密度也用更主动的 enrich
            out += q["enrich_tease_high"] if aff >= 22 else q["enrich_tease"]
    return out


def finalize_reply_for_leave(reply: str, has_leave_intent: bool) -> str:
    if not has_leave_intent:
        return (reply or "").strip()
    return force_single_bubble(reply or "")


def compress_must_focus(text: str, max_len: int = 48) -> str:
    """把必答焦点压成短串；多问时保留多个要点（分号分隔），避免只留第一问。"""
    s = (text or "").strip()
    if not s:
        return ""
    s = re.split(r"[\n\r]+", s, maxsplit=1)[0].strip() or s
    # 已是分号列举 → 截断每项后拼回
    if re.search(r"[;；]", s):
        parts = [p.strip() for p in re.split(r"[;；]+", s) if p.strip()]
        clipped = [re.sub(r"\s+", " ", p)[: max(12, max_len // 2)] for p in parts[:3]]
        return "; ".join(clipped)[: max(max_len * 2, 96)]
    asks = count_user_asks(s)
    if asks >= 2:
        foci = extract_ask_foci(s, max_foci=3, max_len=max(16, max_len // 2))
        if len(foci) >= 2:
            return "; ".join(foci)[: max(max_len * 2, 96)]
    parts = re.split(r"[.。!！?？;；]+", s)
    core = (parts[0] if parts else s).strip() or s
    core = re.sub(r"\s+", " ", core).strip()
    return core[:max_len]


def filter_fact_language(facts: List[str], user_input: str, lang: str = "zh") -> List[str]:
    """事实语言门禁（与 agent._filter_facts_by_user_lang 对齐的轻量版）。"""
    out: List[str] = []
    ui = user_input or ""
    for f in facts or []:
        s = str(f).strip()
        if not s:
            continue
        has_ko = bool(re.search(r"[\uac00-\ud7af]", s))
        has_ja = bool(re.search(r"[\u3040-\u30ff]", s))
        has_zh = bool(re.search(r"[\u4e00-\u9fff]", s))
        if lang == "zh" and (has_ko or (has_ja and not has_zh)):
            continue
        if re.search(r"[\u4e00-\u9fff]", ui) and has_ko and not has_zh:
            continue
        out.append(s)
    return out
