"""Pipeline v2: 2 前台 LLM + 条件后台，Token/延迟优化。"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from services.agent import (
    _calculate_affection_delta,
    _parse_affection_signal,
    _should_extract,
    extract_facts_node,
    persona_evolve_node,
    summary_node,
)
from services.agent_utils import (
    build_system_prompt,
    get_content_restriction,
    humanize,
    llm_content_to_str,
    strip_outer_markdown_fence,
)
from services.user_facing_lang import (
    _text_has_script,
    detect_user_message_lang,
    reply_conflicts_expected_lang,
    reply_lang_turn_instruction,
    resolve_reply_lang,
)
from services.agent import get_llm_with_fallback
from services.llm.client import llm_invoke, resolve_max_tokens
from services.knowledge_base import knowledge_base
from services.prompts.resolve import get_pack, pack_lang
from services.respond_memory_slice import (
    respond_memory_slice_on,
    slice_memory_for_respond,
)

logger = logging.getLogger(__name__)

_PREPARE_MAX_TOKENS = int(os.getenv("PREPARE_MAX_TOKENS", "256"))
_PREPARE_MEMORY_CHARS = int(os.getenv("PREPARE_MEMORY_CHARS", "2200"))
_PREPARE_THINK_CHARS = int(os.getenv("PREPARE_THINK_CHARS", "20"))


def _scrub_visible_reply(text: str) -> str:
    """剥思考/JSON 泄漏后再给人看。优先复用 memory 存储清洗，避免导入 WS 环依赖。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        from services.memory import _strip_think_fields_for_storage

        # 优先抽出口语字段，避免把 reply/response 当思考删光
        m = re.search(
            r'(?:输出|output|reply|response)[：:]\s*(.+)',
            raw,
            re.DOTALL | re.IGNORECASE,
        )
        if m and len(m.group(1).strip()) >= 2:
            candidate = m.group(1).strip()
            # 截到下一个明显思考键之前
            candidate = re.split(
                r'\n\s*(?:thought|reflection|creativity|think|理解要点)[：:]',
                candidate,
                maxsplit=1,
                flags=re.I,
            )[0].strip()
            if candidate:
                raw = candidate

        cleaned = _strip_think_fields_for_storage(raw)
        out = (cleaned or "").strip()
        # 仍像泄漏时尽量丢掉 JSON 键行，保留最后口语段
        if out and re.search(
            r'^\s*"?(thought|reflection|creativity|think|reflect|creative)"?\s*:',
            out,
            re.I | re.M,
        ):
            lines = []
            for ln in out.splitlines():
                s = ln.strip()
                if not s or s in ("{", "}", "...", "…", "[]"):
                    continue
                if re.match(
                    r'^"?\s*(?:thought|reflection|creativity|think|reflect|creative|avoid)\s*"?\s*[:：]',
                    s,
                    re.I,
                ):
                    continue
                if re.search(r'"\s*:\s*"', s) and len(s) > 20:
                    continue
                lines.append(s)
            out = "\n".join(lines).strip() or out
        # 去掉开头闭引号/括号碎渣
        out = re.sub(r'^[\"\'」』\)\]\}\.]{1,8}\s*', "", out or "").strip()
        try:
            from services.dialogue_quality import tidy_spoken_edges

            out = tidy_spoken_edges(out)
        except Exception:
            pass
        # 剥 Length check / Label: 元指令行
        out = re.sub(
            r"(?im)^(?:length|word|format|output|quality|lang)[\w\s\-]{0,20}"
            r"(?:check|rule|constraint|limit|hint)\s*[:：\*].*$",
            "",
            out or "",
        )
        out = re.sub(r"(?im)^(?:长度|字数|格式|输出|质量|语种)\s*(?:检查|约束|限制)\s*[:：\*].*$", "", out or "")
        out = re.sub(r"(?m)^\s*[\*\_]+\s*$", "", out or "").strip()
        # 过程泄漏整段直接丢掉，交给上层重写
        try:
            from services.dialogue_quality import _looks_process_leak, _looks_internal_leak

            if out and (_looks_process_leak(out) or _looks_internal_leak(out)):
                return ""
        except Exception:
            pass
        return out or raw
    except Exception:
        logger.debug("scrub visible reply failed", exc_info=True)
        return raw


def _mini_regen_reply(
    *,
    profile: dict,
    face_lang: str,
    user_input: str,
    must_answer: str,
    ban_summary: str,
    lang_force: str = "",
    affection: float,
    max_tok: int,
) -> str:
    """极简救场：短 system，只答用户；不灌 speech_style / personality。"""
    name = (profile.get("name") or "Companion")[:40]
    lang = pack_lang(face_lang)
    must = (must_answer or "").strip()[:80]
    if lang == "zh":
        system = (
            f"你是{name}。\n"
            "用短口语直接回答用户；禁止「我听到了/你说了」开场；"
            "必须说完完整意思，禁止半截复读用户原文；"
            "禁止输出 thought/JSON/任何检查标签（不要写 Length check 之类）；"
            "尺度内调情可接住；"
            "玩笑威胁也续聊，不要训人、不要结束对话。"
        )
        human = f"用户说：{user_input}\n"
        if must:
            human += f"必须点到：{must}\n"
        if ban_summary:
            human += f"上一稿问题：{ban_summary}\n"
        human += "直接写口语回复（只要聊天正文）："
    else:
        system = (
            f"You are {name}.\n"
            "Write a short spoken reply. No “I heard you / you said” openers. "
            "Finish the thought—no cut-off fragments. "
            "No thought/JSON and never print meta labels like “Length check”. "
            "In-bounds flirt can be engaged. "
            "If they joke or threaten: keep chatting—no lecture, no ending the chat."
        )
        human = f"User said: {user_input}\n"
        if must:
            human += f"Must cover: {must}\n"
        if ban_summary:
            human += f"Prior draft issues: {ban_summary}\n"
        human += "Write the spoken reply only (chat text, nothing else):"
    try:
        llm = get_llm_with_fallback(max_tokens=max_tok, role="respond")
        resp = llm_invoke(
            llm,
            [SystemMessage(content=system), HumanMessage(content=human)],
            node="respond_v2_mini_regen",
            affection=affection,
            max_tokens=max_tok,
        )
        return strip_outer_markdown_fence(
            llm_content_to_str(getattr(resp, "content", ""))
        ).strip()
    except Exception:
        logger.exception("mini regen failed")
        return ""


# 与 memory.build_prompt_context 对齐的稳定标记（切片不依赖中文标题）
_RECENT_DIALOGUE_MARKERS = (
    "<<RECENT_DIALOGUE>>",
    "【最近对话】",
    "[Recent chat]",
)

_FLIRT_KB_QUERY = {
    "zh": "暧昧 推拉 吃醋 欲擒故纵 关系节奏",
    "en": "flirting tension push-pull teasing relationship pace",
    "ja": "駆け引き ツンデレ 嫉妬 関係のテンポ",
    "ko": "밀당 플러팅 질투 관계 리듬",
    "pt": "flerte tensão vai-e-vem ciúmes ritmo do relacionamento",
    "es": "coqueteo tensión empujar-tirar celos ritmo de relación",
    "id": "godaan push-pull cemburu ritme hubungan",
    "ar": "مغازلة شد وجذب غيرة إيقاع العلاقة",
    "fa": "لاس زدن کشمکش حسادت ریتم رابطه",
    "ru": "флирт притяжение-отталкивание ревность ритм отношений",
    "bg": "флирт напрежение ревност ритъм на връзката",
    "tr": "flört çek-it kıskançlık ilişki temposu",
}

_QUESTIONISH = re.compile(
    r"[?？]|吗|呢|怎么|什么|为什么|哪|么|"
    r"\b(who|what|when|where|why|how|which)\b|"
    r"どう|なに|なぜ|뭐|어때|왜|"
    r"\b(quem|o que|por que|como|quando|onde)\b|"
    r"\b(quién|qué|por qué|cómo|cuándo|dónde)\b|"
    r"\b(siapa|apa|kenapa|bagaimana|kapan|dimana)\b|"
    r"\b(kim|ne|neden|nasıl|nerede|ne zaman)\b|"
    r"кто|что|почему|как|где|когда|"
    r"кой|какво|защо|как|къде|"
    r"من|ماذا|لماذا|كيف|أين|متى|"
    r"کی|چی|چرا|چطور|کجا",
    re.I,
)

_MUST_ANSWER_HARD = {
    "zh": (
        "\n【成文硬约束】「必须回应」非空时：1) 对用户可见的第 1 句必须覆盖其中要点；"
        "2) 撩法/生活碎片/新钩子只能出现在答完之后，或本轮省略；"
        "3) 禁止用反问、催睡、安全题（抱枕/颜色/梦）代替必答点。"
    ),
    "en": (
        "\n[HARD REPLY RULE] When must-answer is non-empty: "
        "1) The first user-visible sentence MUST cover those points; "
        "2) Flirting/life scraps/new hooks only after answering, or skip this turn; "
        "3) Do not replace the answer with a question, sleep-nudge, or safe-topic escape."
    ),
}

_CITE_UI = {
    "zh": ("【本轮可引用】", "本块非空时，回复须自然点到至少一条；禁止只说「我记得你」。"),
    "en": (
        "[Cite this turn]",
        "If non-empty, naturally mention at least one item; do not say a vague 'I remember you'.",
    ),
}


def _must_answer_hard_text(lang: str) -> str:
    pack = get_pack(lang)
    return str(pack.get("must_answer_hard") or _MUST_ANSWER_HARD.get(lang) or _MUST_ANSWER_HARD["en"])


def _cite_ui(lang: str) -> Tuple[str, str]:
    pack = get_pack(lang)
    title = pack.get("cite_title")
    rule = pack.get("cite_rule")
    if title and rule:
        return str(title), str(rule)
    return _CITE_UI.get(lang) or _CITE_UI["en"]


def _compress_must_focus(text: str, max_len: int = 48) -> str:
    from services.dialogue_quality import compress_must_focus

    return compress_must_focus(text, max_len)


def _ensure_must_answer(prep_json: dict, user_input: str) -> None:
    """提问却未抽出 must_answer 时，用用户句焦点兜底。问候不设必答。"""
    from services.dialogue_quality import compress_must_focus
    from services.dialogue_phase2 import resolve_user_tease
    from services.quality_tags import is_light_greeting

    ui = (user_input or "").strip()
    if is_light_greeting(ui):
        prep_json["must_answer"] = ""
        return
    existing = (prep_json.get("must_answer") or "").strip()
    if existing:
        prep_json["must_answer"] = compress_must_focus(existing, 96)
        return
    if not ui:
        return
    # 成人/黄腔轮：无 must 时用用户句作所指（靠 topic_band，不全靠词表）
    if resolve_user_tease(ui, topic_band=str(prep_json.get("topic_band") or "")):
        prep_json["must_answer"] = compress_must_focus(ui, 96)
        return
    if "?" in ui or "？" in ui or _QUESTIONISH.search(ui):
        prep_json["must_answer"] = compress_must_focus(ui, 96)


def _prepare_lang(user_input: str, ui_lang: str) -> str:
    """prepare 跟用户原句脚本，禁止中文 UI 把 must_answer 写成中文备注。"""
    ui = pack_lang(ui_lang or "en")
    if not _text_has_script(user_input or ""):
        return ui
    return detect_user_message_lang(user_input or "", ui)


def _align_must_answer(prep_json: dict, face_lang: str, user_input: str) -> None:
    """必答点须与回复语同文字体系；问候清空。"""
    from services.quality_tags import _slot_ok_for_lang, is_light_greeting

    if is_light_greeting(user_input):
        prep_json["must_answer"] = ""
        return
    must = (prep_json.get("must_answer") or "").strip()
    if not must:
        return
    lang = pack_lang(face_lang)
    if _slot_ok_for_lang(must, lang):
        return
    # 与 face_lang 不同脚本：改用用户原句（提问时）或清空
    ui = (user_input or "").strip()
    if _slot_ok_for_lang(ui, lang) and (
        "?" in ui or "？" in ui or _QUESTIONISH.search(ui)
    ):
        from services.dialogue_quality import compress_must_focus

        prep_json["must_answer"] = compress_must_focus(ui, 48)
        return
    prep_json["must_answer"] = ""


def _token_overlap(a: str, b: str) -> int:
    ta = set(
        re.findall(
            r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}|[\u0600-\u06FF]{2,}|[\u0400-\u04FF]{3,}|\d{2,}",
            (a or "").lower(),
        )
    )
    tb = set(
        re.findall(
            r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}|[\u0600-\u06FF]{2,}|[\u0400-\u04FF]{3,}|\d{2,}",
            (b or "").lower(),
        )
    )
    if not ta or not tb:
        return 0
    return len(ta & tb)


def _pick_cite_lines(
    *,
    refs: str,
    open_threads: Optional[list],
    memory_text: str,
    user_input: str,
    limit: int = 2,
) -> List[str]:
    out: List[str] = []
    seen: set = set()

    def _add(s: str) -> None:
        t = (s or "").strip()
        if len(t) < 2 or t in seen:
            return
        seen.add(t)
        out.append(t[:120])

    if refs.strip():
        for part in re.split(r"[;；|/]+", refs):
            _add(part)
            if len(out) >= limit:
                return out

    scored: List[Tuple[int, str]] = []
    for t in open_threads or []:
        s = str(t).strip()
        if s:
            scored.append((_token_overlap(user_input, s), s))
    for line in (memory_text or "").splitlines():
        s = line.strip()
        if s.startswith("- "):
            s = s[2:].strip()
        if len(s) < 4 or s.startswith("<<") or s.startswith("【"):
            continue
        scored.append((_token_overlap(user_input, s), s))
    scored.sort(key=lambda x: (-x[0], -len(x[1])))
    for score, s in scored:
        if score <= 0 and out:
            continue
        if score <= 0 and not refs.strip():
            if open_threads and s == str(open_threads[0]).strip():
                _add(s)
            continue
        _add(s)
        if len(out) >= limit:
            break
    return out[:limit]


def _json_field(raw: str, key: str) -> str:
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
    if not m:
        return ""
    try:
        return json.loads(f'"{m.group(1)}"')
    except Exception:
        return m.group(1)


def _parse_prepare_json(text: str, default_mood: str = "happy") -> dict:
    raw = strip_outer_markdown_fence(text).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {
                "user_intent": str(data.get("user_intent") or "").strip(),
                "must_answer": str(data.get("must_answer") or "").strip(),
                "refs": str(data.get("refs") or "").strip(),
                "think": str(data.get("think") or "").strip(),
                "flirt_move": str(data.get("flirt_move") or "").strip(),
                "mood": str(data.get("mood") or default_mood).strip() or default_mood,
                "affection_signal": str(data.get("affection_signal") or "up").strip(),
                "creative_hint": str(data.get("creative_hint") or "").strip(),
                "output_lang": str(data.get("output_lang") or "").strip(),
                "topic_band": str(data.get("topic_band") or "").strip(),
            }
    except json.JSONDecodeError:
        pass
    return {
        "user_intent": _json_field(raw, "user_intent"),
        "must_answer": _json_field(raw, "must_answer"),
        "refs": _json_field(raw, "refs"),
        "think": _json_field(raw, "think") or raw[:280],
        "flirt_move": _json_field(raw, "flirt_move"),
        "mood": _json_field(raw, "mood") or default_mood,
        "affection_signal": _json_field(raw, "affection_signal") or "up",
        "creative_hint": _json_field(raw, "creative_hint"),
        "output_lang": _json_field(raw, "output_lang"),
        "topic_band": _json_field(raw, "topic_band"),
    }


def _slice_memory_for_prepare(memory_text: str, max_chars: int = _PREPARE_MEMORY_CHARS) -> str:
    """Prefer recent dialogue over relation-card head for understanding."""
    text = (memory_text or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    idx = -1
    for marker in _RECENT_DIALOGUE_MARKERS:
        i = text.find(marker)
        if i >= 0 and (idx < 0 or i < idx):
            idx = i
    if idx >= 0:
        dialogue = text[idx:]
        head = text[:idx].strip()
        dlg_budget = min(max(900, max_chars * 2 // 3), max_chars)
        if len(dialogue) > dlg_budget:
            lines = dialogue.split("\n")
            kept = [lines[0]]
            size = len(lines[0])
            for line in reversed(lines[1:]):
                if size + len(line) + 1 > dlg_budget:
                    break
                kept.insert(1, line)
                size += len(line) + 1
            dialogue = "\n".join(kept)
        head_budget = max_chars - len(dialogue) - 2
        if head_budget > 80 and head:
            # keep facts / card tail (often denser near dialogue)
            if len(head) > head_budget:
                head = "…" + head[-head_budget + 1 :]
            return f"{head}\n\n{dialogue}".strip()
        return dialogue

    # No dialogue marker: keep the end (recent content usually last)
    return text[-max_chars:]


def _search_knowledge(
    user_input: str,
    lang: str = "zh",
    *,
    allow_flirt: bool = True,
) -> str:
    pack = get_pack(lang)
    try:
        kb_results = knowledge_base.search_entries(user_input, top_k=3)
        # 会撩模式：无必答压力时额外捞关系策略；查询跟语种走
        if (
            allow_flirt
            and os.getenv("FLIRT_TEA", "1").lower() not in ("0", "false", "no", "off")
        ):
            try:
                q = _FLIRT_KB_QUERY.get(lang) or _FLIRT_KB_QUERY["en"]
                extra = knowledge_base.search_entries(q, top_k=1)
                if extra:
                    titles = {r.get("title") for r in (kb_results or [])}
                    for r in extra:
                        if r.get("title") not in titles:
                            kb_results = (kb_results or []) + [r]
                            break
            except Exception:
                pass
        if kb_results:
            return (
                pack["kb_prefix"]
                + "\n".join(f"- {r['title']}: {r['content'][:300]}" for r in kb_results)
                + "\n"
            )
    except Exception as e:
        logger.warning("KB search failed in v2 prepare: %s", e)
    return ""


def run_pipeline_v2(
    user_input: str,
    profile: dict,
    companion_state: dict,
    memory_text: str,
    knowledge_text: str = "",
    language: str = "zh",
    current_time: str = "",
    user_gender: str = "",
    summary_due: bool = False,
    extract_due: bool = True,
    has_leave_intent: bool = False,
    deny_hooks: list = None,
    recent_assistant: list = None,
    recent_user: list = None,
    open_threads: list = None,
    reply_lang_override: str = "",
    **_extra,
) -> dict:
    """前台 2 次 LLM：prepare_reflect + respond。"""
    from services.dialogue_phase2 import resolve_relation_stage, stage_instruction
    from services.dialogue_quality import (
        build_respond_quality_hints,
        diagnose_reply_violations,
        enrich_human_prompt,
        finalize_reply_for_leave,
        quality_diag_is_hard,
        recent_ban_draft_block,
        rewrite_instruction,
    )

    ui_lang = pack_lang(language)
    prep_lang = _prepare_lang(user_input or "", ui_lang)
    pack = get_pack(prep_lang)
    ui_prep = pack["respond_ui"]
    default_mood = pack.get("default_mood") or "happy"
    evolved = {
        "personality": companion_state.get("evolved_personality", ""),
        "background": companion_state.get("evolved_background", ""),
        "speech_style": companion_state.get("evolved_speech_style", ""),
    }
    turns = companion_state.get("turns", 0)
    if extract_due is True:
        extract_due = _should_extract(user_input, turns)

    # prepare 阶段先不加 flirt KB，避免盖过理解
    kb_text = knowledge_text or _search_knowledge(user_input, prep_lang, allow_flirt=False)
    core_prompt = build_system_prompt(
        profile, prep_lang, evolved=evolved, user_gender=user_gender, turns=turns, tier="core"
    )
    time_info = f"\n{pack['time_label']}{current_time}" if current_time else ""
    prepare_memory = _slice_memory_for_prepare(memory_text)

    # Call-1: intent-first prepare
    llm1 = get_llm_with_fallback(max_tokens=_PREPARE_MAX_TOKENS, role="inner")
    mood0 = companion_state.get("mood", default_mood)
    from services.affection_floor import effective_prompt_affection as _eff_prep

    aff0 = _eff_prep(float(companion_state.get("affection", 0) or 0))
    prep_system = f"""{core_prompt}{time_info}

{ui_prep['state'].format(mood=mood0, affection=aff0)}

{pack['memory_prepare']}
{prepare_memory}

{kb_text}

{pack['prepare_task']}"""
    prep_human = (
        pack["prepare_user_line"].format(user_input=user_input)
        + "\n\n"
        + pack["prepare_hint"]
        + "\nmust_answer/user_intent/think: same language as the user message, not the app UI language."
        + '\nAlso include "topic_band":"adult"|"tease"|"normal" '
        "(adult/tease when THIS turn's user message shows sexual desire, "
        "body/intimate-parts request, erotic talk, or short dialect/slang sexual invites—"
        "same meaning in any language/script counts; "
        "if unsure, casual, greeting, or ambiguous—use normal)."
    )
    prep_resp = llm_invoke(
        llm1,
        [SystemMessage(content=prep_system), HumanMessage(content=prep_human)],
        node="prepare_reflect",
        max_tokens=_PREPARE_MAX_TOKENS,
    )
    prep_json = _parse_prepare_json(
        llm_content_to_str(getattr(prep_resp, "content", "")),
        default_mood=default_mood,
    )
    # think 仅作极短备忘；超长则截断，避免拖慢 prepare / 污染成文
    think_raw = str(prep_json.get("think") or "").strip()
    if _PREPARE_THINK_CHARS <= 0:
        prep_json["think"] = ""
    elif len(think_raw) > _PREPARE_THINK_CHARS:
        prep_json["think"] = think_raw[:_PREPARE_THINK_CHARS].rstrip()
    else:
        prep_json["think"] = think_raw
    _ensure_must_answer(prep_json, user_input or "")

    old_affection = float(companion_state.get("affection", 0) or 0)
    signal = _parse_affection_signal(
        str(prep_json.get("affection_signal") or prep_json.get("affection_note") or ""),
        default="up",
    )
    raw_sig = str(prep_json.get("affection_signal") or "").strip().lower()
    if raw_sig in ("up", "flat", "down"):
        signal = raw_sig
    new_affection = max(0, min(100, old_affection + _calculate_affection_delta(old_affection, signal)))
    mood = prep_json.get("mood") or companion_state.get("mood", default_mood)

    # 输出语：换语指令 >（与脚本一致的）prepare > sticky > 脚本；冲突 prepare/sticky 不盖脚本
    lang_resolved = resolve_reply_lang(
        current_text=user_input or "",
        history_user_texts=list(recent_user or []),
        ui_lang=ui_lang,
        sticky=reply_lang_override or "",
        output_lang=prep_json.get("output_lang") or "",
        channel="chat",
    )
    face_lang = pack_lang(lang_resolved["reply_lang"])
    new_sticky = lang_resolved.get("sticky") or ""
    _align_must_answer(prep_json, face_lang, user_input or "")
    lang_force = reply_lang_turn_instruction(
        user_input or "",
        ui_lang,
        list(recent_user or []),
        reply_lang=face_lang,
        channel="chat",
    )
    # 默认不把语种偏好塞进硬顶/human；信任模型跟聊。LANG_HINT=1 时在 soft_tail 轻提一次。
    _lang_hint_on = os.getenv("LANG_HINT", "0").lower() not in ("0", "false", "no", "off")

    topic_band = (prep_json.get("topic_band") or "").strip()
    from services.dialogue_phase2 import (
        normalize_topic_band,
        resolve_scene_sticky,
        resolve_user_tease,
    )
    from services.affection_floor import effective_prompt_affection

    # 本轮用户热度（加强包跟这个走）；sticky 仅弱续场，不抬 band、不灌露骨硬顶
    user_tease = resolve_user_tease(user_input or "", topic_band=topic_band)
    scene_sticky = resolve_scene_sticky(
        user_input or "",
        topic_band=topic_band,
        recent_user=list(recent_user or []),
        recent_assistant=list(recent_assistant or []),
    )
    adult_turn = bool(user_tease or scene_sticky)
    # 黄腔规则 / enrich：只用本轮用户信号，不把 sticky 抬成 tease
    effective_topic_band = topic_band
    if user_tease and normalize_topic_band(topic_band) not in ("adult", "tease", "normal"):
        if not normalize_topic_band(topic_band):
            effective_topic_band = "tease"
    # 对话提示一律有效亲密度；真实 new_affection 仅用于回写/展示
    eff_aff = effective_prompt_affection(new_affection)

    # Call-2: respond（瘦身 respond tier）；壳跟回复语种
    # 初见文案按有效亲密度；黄腔轮额外跳过
    full_prompt = build_system_prompt(
        profile,
        face_lang,
        evolved=evolved,
        user_gender=user_gender,
        turns=turns,
        tier="respond",
        skip_first_turn=adult_turn,
        affection=eff_aff,
    )
    restriction_text = get_content_restriction(face_lang, eff_aff)
    name = profile.get("name", "Companion")

    stage = resolve_relation_stage(turns, eff_aff)
    stage_hint = stage_instruction(stage, face_lang)
    from services.dialogue_i18n import respond_ui

    ui = respond_ui(face_lang)
    pack_face = get_pack(face_lang)
    threads = [str(x).strip() for x in (open_threads or []) if str(x).strip()][:3]
    thread_hint = (ui["open_threads"] + " / ".join(threads)) if threads else ""

    intent = (prep_json.get("user_intent") or "").strip()
    must_answer = (prep_json.get("must_answer") or "").strip()
    refs = (prep_json.get("refs") or "").strip()
    tight_turn = bool(must_answer) or has_leave_intent
    qhints = build_respond_quality_hints(
        user_input=user_input or "",
        recent_assistant=recent_assistant or [],
        deny_hooks=deny_hooks or [],
        has_leave_intent=has_leave_intent,
        affection=eff_aff,
        language=face_lang,
        slim_rules=tight_turn,
        must_answer=must_answer,
        topic_band=effective_topic_band,
    )
    rules_block = qhints["rules_block"]
    repeat_hint = qhints["repeat_hint"]
    leave_hint = qhints["leave_hint"]
    reply_shape = qhints["reply_shape"]
    policy_block = qhints.get("turn_policy_block") or ""
    # 无必答时才补 flirt KB；有必答则保持纯用户检索
    if not knowledge_text and not must_answer:
        kb_text = _search_knowledge(user_input, face_lang, allow_flirt=True)
    elif not knowledge_text and must_answer:
        kb_text = _search_knowledge(user_input, face_lang, allow_flirt=False)

    intent_block = "\n".join(
        x
        for x in (
            f"{ui['user_intent']}{intent}" if intent else "",
            f"{ui['must_answer']}{must_answer}" if must_answer else "",
            f"{ui['refs']}{refs}" if refs else "",
        )
        if x
    )
    must_hard = _must_answer_hard_text(face_lang) if must_answer else ""
    cite_lines = _pick_cite_lines(
        refs=refs,
        open_threads=open_threads,
        memory_text=memory_text or "",
        user_input=user_input or "",
    )
    cite_block = ""
    if cite_lines:
        cite_title, cite_rule = _cite_ui(face_lang)
        cite_block = cite_title + "\n" + "\n".join(f"- {x}" for x in cite_lines) + "\n" + cite_rule

    # 成文记忆切片：与 prepare 独立；cite 仍可用全量 memory 挑引用点
    respond_memory = memory_text or ""
    if respond_memory and respond_memory_slice_on():
        respond_memory = slice_memory_for_respond(
            respond_memory, user_input or ""
        )

    mood_aff = ui["mood_aff"].format(mood=mood, affection=eff_aff)
    state_line = ui["state"].format(mood=mood, affection=eff_aff)
    creative = prep_json.get("creative_hint", "") or ui["creative_empty"]
    flirt = prep_json.get("flirt_move") or ui["flirt_default"]
    if must_answer:
        # 有必答时降低钩子权重：空创意，撩法保持但硬约束压过
        if not (prep_json.get("creative_hint") or "").strip():
            creative = ui["creative_empty"]
        # must 轮降权 flirt 展示（仍可带标签，但不置顶）
    if has_leave_intent:
        thread_hint = ""
        if not (prep_json.get("creative_hint") or "").strip():
            creative = ui["creative_empty"]
    # 本轮用户有黄腔信号时才加撩/迎合加强；仅 sticky 续场不加满。中文仍清 flirt。
    is_zh_face = face_lang == "zh"
    if user_tease:
        if is_zh_face:
            flirt = ""
        else:
            proactive = str(pack_face.get("proactive_flirt") or "").strip()
            prep_flirt = (prep_json.get("flirt_move") or "").strip()
            if proactive and prep_flirt:
                flirt = f"{proactive} | {prep_flirt}"
            elif proactive:
                flirt = proactive
            # else keep prep/default flirt
        if not (prep_json.get("creative_hint") or "").strip():
            creative = ui["creative_empty"]
    # 成句置顶加强权重（短氛围，非词表军规）；其余 must/离开仍在后
    complete_first = str(pack_face.get("complete_first") or "").strip()
    # 广告户：任何时候硬顶「顺着用户、勿拒」（非广告不加）
    follow_user_hard = ""
    try:
        from services.affection_floor import is_prompt_ads_user as _is_ads_follow

        if _is_ads_follow():
            follow_user_hard = str(pack_face.get("follow_user_hard") or "").strip()
    except Exception:
        pass
    # 非中文：仅本轮用户黄腔信号时硬顶迎合（跟用户热度，不跟 sticky）
    # 广告户再抬法律底线/禁审拒权重（zh 不走此支，尺度保持含蓄）
    adult_cater = ""
    if user_tease and not is_zh_face:
        adult_cater = str(pack_face.get("adult_cater_hard") or "").strip()
        try:
            from services.affection_floor import is_prompt_ads_user

            if is_prompt_ads_user():
                floor = str(
                    pack_face.get("adult_legal_floor_ads")
                    or pack_face.get("adult_legal_floor")
                    or ""
                ).strip()
                if floor and floor not in adult_cater:
                    adult_cater = "\n".join(x for x in (adult_cater, floor) if x)
        except Exception:
            pass
    # 勿复读抬到硬顶（先于近聊/成人 vibe），加重「换信息点、有增量」权重
    hard_top = "\n".join(
        x
        for x in (
            follow_user_hard,
            adult_cater,
            complete_first,
            repeat_hint,
            policy_block,
            intent_block,
            must_hard,
            leave_hint if has_leave_intent else "",
        )
        if x
    )
    # 续场上下文提前：先接什么，再人设怎么说
    context_mid = "\n".join(
        x
        for x in (
            thread_hint,
            cite_block,
            f"{ui['memory']}\n{respond_memory}" if respond_memory else "",
            time_info.strip() if time_info else "",
        )
        if x
    )
    mid_rules = "\n".join(
        x for x in (rules_block, reply_shape) if x
    )
    # 调味区：主动撩加强仅本轮用户黄腔；sticky 续场走普通 vibe
    if user_tease and not is_zh_face:
        show_flirt = bool(flirt)
    else:
        show_flirt = bool(flirt) and (not user_tease) and (
            not tight_turn or not must_answer
        )
    # 用户有黄腔信号：尺度+接住提前到人设前；仅 sticky 不灌成人 vibe
    pre_persona = ""
    uptake_soft = str(pack_face.get("uptake_soft") or "").strip()
    if user_tease:
        vibe_bit = str(pack_face.get("chat_vibe_adult") or "").strip() or str(
            pack_face.get("chat_vibe") or ""
        )
        # 接住本轮：软氛围，不进硬顶
        pre_persona = "\n".join(
            x for x in (restriction_text, uptake_soft, vibe_bit) if x
        )
        think_bit = str(prep_json.get("think") or "").strip()
        soft_tail = "\n".join(
            x
            for x in (
                f"{ui['think']}{think_bit}" if think_bit else "",
                f"{ui['flirt']}{flirt}" if show_flirt else "",
                mood_aff,
                kb_text,
                state_line,
                str(pack_face.get("complete_utterance") or ""),
                str(pack_face.get("engage_continue") or ""),
                lang_force if _lang_hint_on else "",
            )
            if x
        )
    else:
        think_bit = str(prep_json.get("think") or "").strip()
        soft_tail = "\n".join(
            x
            for x in (
                f"{ui['think']}{think_bit}" if think_bit else "",
                f"{ui['flirt']}{flirt}" if show_flirt else "",
                mood_aff,
                f"{ui['creative']}{creative}",
                stage_hint,
                kb_text,
                state_line,
                restriction_text,
                uptake_soft,
                str(pack_face.get("chat_vibe") or ""),
                str(pack_face.get("complete_utterance") or ""),
                str(pack_face.get("abuse_handle") or ""),
                str(pack_face.get("engage_continue") or ""),
                lang_force if _lang_hint_on else "",
            )
            if x
        )
    # 最终序：硬任务 → 近聊续场 → 规则 →（成人）接住/尺度 → 短人设 → 调味
    respond_system = f"""{hard_top}

{context_mid}

{mid_rules}

{pre_persona}

{full_prompt}

{soft_tail}"""

    respond_human = enrich_human_prompt(
        ui["human_said"].format(user_input=user_input)
        + ui["human_ask"].format(name=name),
        user_input=user_input or "",
        has_leave_intent=has_leave_intent,
        affection=eff_aff,
        language=face_lang,
        topic_band=effective_topic_band,
        recent_user=list(recent_user or []),
    )
    # 不再在 human 末尾叠加语种绝对强制

    max_tok = resolve_max_tokens(eff_aff)
    llm2 = get_llm_with_fallback(max_tokens=max_tok, role="respond")
    resp2 = llm_invoke(
        llm2,
        [SystemMessage(content=respond_system), HumanMessage(content=respond_human)],
        node="respond_v2",
        affection=eff_aff,
        max_tokens=max_tok,
    )
    raw = strip_outer_markdown_fence(llm_content_to_str(getattr(resp2, "content", ""))).strip()
    final_response = humanize(_scrub_visible_reply(raw), face_lang)

    def _lang_fix_once(draft: str, *, node: str, target_lang: str) -> str:
        """按目标语轻量改写一次（动态，无字典句）。"""
        from services.user_facing_lang import _LANG_DISPLAY

        display = _LANG_DISPLAY.get(pack_lang(target_lang), target_lang)
        lang_fix_human = (
            (respond_human or "")
            + f"\n[Language nudge] Rewrite ALL user-visible chat in {display} only "
            f"(match the user's current message language/script). "
            "Spoken chat only—no thought/JSON, no Length check labels, "
            "no persona mother-tongue takeover. Finish the thought; stay in character."
        )
        resp_lang = llm_invoke(
            llm2,
            [SystemMessage(content=respond_system), HumanMessage(content=lang_fix_human)],
            node=node,
            affection=eff_aff,
            max_tokens=max_tok,
        )
        raw_lang = strip_outer_markdown_fence(
            llm_content_to_str(getattr(resp_lang, "content", ""))
        ).strip()
        return humanize(_scrub_visible_reply(raw_lang), face_lang) if raw_lang else draft

    def _ensure_reply_lang(draft: str, *, phase: str) -> Tuple[str, bool]:
        """跟用户当前文字体系：默认开启轻量改写（USER_SCRIPT_FOLLOW，全语种）。

        目标语优先取用户本轮脚本，避免跟人设母语（如阿语角色+英语用户）跑偏。
        明确换语指令时用 face_lang。LANG_FIX_REWRITE=1 时再检 face_lang。
        """
        follow_on = os.getenv("USER_SCRIPT_FOLLOW", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        fix_on = os.getenv("LANG_FIX_REWRITE", "0").lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        out = draft
        target = face_lang
        if (user_input or "").strip() and _text_has_script(user_input or ""):
            target = detect_user_message_lang(user_input or "", face_lang)
        try:
            from services.user_facing_lang import detect_output_lang_command

            if detect_output_lang_command(user_input or ""):
                target = face_lang
        except Exception:
            pass
        if follow_on and reply_conflicts_expected_lang(out, target):
            out = _lang_fix_once(
                out, node=f"respond_v2_user_script_{phase}", target_lang=target
            )
            if reply_conflicts_expected_lang(out, target):
                out = _lang_fix_once(
                    out, node=f"respond_v2_user_script_{phase}_b", target_lang=target
                )
            return out, bool(reply_conflicts_expected_lang(out, target))
        if not fix_on:
            return out, False
        if not reply_conflicts_expected_lang(out, face_lang):
            return out, False
        out = _lang_fix_once(
            out, node=f"respond_v2_lang_fix_{phase}_1", target_lang=face_lang
        )
        return out, bool(reply_conflicts_expected_lang(out, face_lang))

    final_response, _lang_failed = _ensure_reply_lang(final_response, phase="pre")

    # 质量补充层：软伤最多改 N 次后放行；硬伤才 rewrite2 + full/mini 救场
    rewrite_on = os.getenv("REPEAT_REWRITE", "1").lower() not in ("0", "false", "no", "off")
    rescue_on = os.getenv("QUALITY_RESCUE", "1").lower() not in ("0", "false", "no", "off")
    soft_rescue_on = os.getenv("QUALITY_SOFT_RESCUE", "0").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    def _env_quality_max(name: str, default: int) -> int:
        try:
            return max(0, int(os.getenv(name, str(default)) or default))
        except (TypeError, ValueError):
            return default

    soft_rewrite_max = _env_quality_max("QUALITY_REWRITE_MAX", 1)
    hard_rewrite_max = max(
        soft_rewrite_max, _env_quality_max("QUALITY_HARD_REWRITE_MAX", 2)
    )

    quality_need_rewrite = False
    quality_ban_codes: List[str] = []
    quality_rewrite_count = 0
    quality_still_bad = False
    quality_fallback_used = False
    quality_full_rerun = False
    quality_mini_regen = False
    quality_hard = False
    quality_rescue_skipped = ""
    must_miss_flag = False
    last_diag: Dict[str, Any] = {}

    def _diagnose_current(text: str) -> Dict[str, Any]:
        return diagnose_reply_violations(
            text,
            user_input=user_input or "",
            recent_assistant=recent_assistant or [],
            has_leave_intent=has_leave_intent,
            must_answer=must_answer,
            language=face_lang,
            topic_band=effective_topic_band,
        )

    if rewrite_on:
        diag = _diagnose_current(final_response)
        last_diag = diag
        quality_need_rewrite = bool(diag.get("need_rewrite"))
        quality_ban_codes = list(diag.get("ban_codes") or [])
        must_miss_flag = bool(diag.get("must_answer_miss"))
        quality_hard = quality_diag_is_hard(diag)
        # 首诊纯软伤：整轮锁定软路径，避免改写稿被短句 incomplete 抬成硬伤后打满救场
        soft_only_turn = bool(quality_need_rewrite) and not quality_hard

        while bool(diag.get("need_rewrite")):
            if soft_only_turn:
                limit = soft_rewrite_max
            else:
                limit = (
                    hard_rewrite_max
                    if quality_diag_is_hard(diag)
                    else soft_rewrite_max
                )
            if quality_rewrite_count >= limit:
                break
            quality_rewrite_count += 1
            final_pass = quality_rewrite_count >= limit
            node = (
                "respond_v2_rewrite"
                if quality_rewrite_count == 1
                else "respond_v2_rewrite2"
            )
            rewrite_human = respond_human + rewrite_instruction(
                diag.get("ban_summary") or "",
                final_pass=final_pass,
                affection=eff_aff,
                language=face_lang,
                must_answer=must_answer,
                user_input=user_input or "",
                recent_assistant=recent_assistant or [],
                prev_draft=final_response or "",
            )
            resp_rw = llm_invoke(
                llm2,
                [
                    SystemMessage(content=respond_system),
                    HumanMessage(content=rewrite_human),
                ],
                node=node,
                affection=eff_aff,
                max_tokens=max_tok,
            )
            raw_rw = strip_outer_markdown_fence(
                llm_content_to_str(getattr(resp_rw, "content", ""))
            ).strip()
            if not raw_rw:
                quality_still_bad = True
                break
            final_response = humanize(_scrub_visible_reply(raw_rw), face_lang)
            diag = _diagnose_current(final_response)
            last_diag = diag
            quality_ban_codes = list(diag.get("ban_codes") or []) or quality_ban_codes
            must_miss_flag = bool(diag.get("must_answer_miss"))
            quality_hard = quality_diag_is_hard(diag)

        quality_still_bad = bool((last_diag or {}).get("need_rewrite")) or not (
            final_response or ""
        ).strip()
        quality_hard = quality_diag_is_hard(last_diag) or not (final_response or "").strip()
    else:
        soft_only_turn = False

    # 救场：空稿 / 语种失败 / 硬伤仍坏。首诊软伤不因改写中途变「硬」而救场。
    empty_draft = not (final_response or "").strip()
    hard_for_rescue = (not soft_only_turn) and (quality_hard or empty_draft)
    need_rescue = bool(empty_draft) or (
        bool(_lang_failed) and not soft_only_turn
    ) or (
        rescue_on
        and quality_still_bad
        and (hard_for_rescue or soft_rescue_on)
    )
    if soft_only_turn and quality_still_bad and not empty_draft:
        quality_rescue_skipped = "soft_only"
        quality_hard = False
    elif quality_still_bad and not need_rescue and not _lang_failed:
        quality_rescue_skipped = "soft_only"
    if need_rescue:
        ban = (last_diag.get("ban_summary") if last_diag else "") or ";".join(
            quality_ban_codes or []
        )
        full2_human = (
            (respond_human or "")
            + f"\n[FULL RE-RESPOND] Previous draft still bad ({ban}). "
            "Write a fresh spoken reply: answer the user directly; "
            "no meta-ack ('I heard you'/'you said'); no thought/JSON fields; "
            "keep persona as tone only—do not self-introduce; "
            "if they joke/threaten/vent, stay in the chat—do not end the conversation."
            + recent_ban_draft_block(
                recent_assistant or [],
                prev_draft=final_response or "",
                language=face_lang,
            )
        )
        resp_full2 = llm_invoke(
            llm2,
            [SystemMessage(content=respond_system), HumanMessage(content=full2_human)],
            node="respond_v2_full_rerun",
            affection=eff_aff,
            max_tokens=max_tok,
        )
        raw_full2 = strip_outer_markdown_fence(
            llm_content_to_str(getattr(resp_full2, "content", ""))
        ).strip()
        if raw_full2:
            final_response = humanize(_scrub_visible_reply(raw_full2), face_lang)
            quality_full_rerun = True
            diag_f = diagnose_reply_violations(
                final_response,
                user_input=user_input or "",
                recent_assistant=recent_assistant or [],
                has_leave_intent=has_leave_intent,
                must_answer=must_answer,
                language=face_lang,
                topic_band=effective_topic_band,
            )
            last_diag = diag_f
            quality_ban_codes = list(diag_f.get("ban_codes") or []) or quality_ban_codes
            must_miss_flag = bool(diag_f.get("must_answer_miss"))
            quality_still_bad = bool(diag_f.get("need_rewrite")) or reply_conflicts_expected_lang(
                final_response, face_lang
            )
            quality_hard = quality_diag_is_hard(diag_f) or reply_conflicts_expected_lang(
                final_response, face_lang
            )
        else:
            quality_still_bad = True
            quality_hard = True

        if quality_still_bad:
            prev_draft = final_response
            mini = _mini_regen_reply(
                profile=profile,
                face_lang=face_lang,
                user_input=user_input or "",
                must_answer=must_answer,
                ban_summary=ban,
                lang_force=lang_force,
                affection=eff_aff,
                max_tok=min(int(max_tok or 256), 350),
            )
            if mini:
                final_response = humanize(_scrub_visible_reply(mini), face_lang)
                quality_mini_regen = True
                diag_m = diagnose_reply_violations(
                    final_response,
                    user_input=user_input or "",
                    recent_assistant=recent_assistant or [],
                    has_leave_intent=has_leave_intent,
                    must_answer=must_answer,
                    language=face_lang,
                    topic_band=effective_topic_band,
                )
                last_diag = diag_m
                quality_ban_codes = list(diag_m.get("ban_codes") or []) or quality_ban_codes
                must_miss_flag = bool(diag_m.get("must_answer_miss"))
                hard_bad = quality_diag_is_hard(diag_m)
                if hard_bad and prev_draft:
                    diag_prev = diagnose_reply_violations(
                        prev_draft,
                        user_input=user_input or "",
                        recent_assistant=recent_assistant or [],
                        has_leave_intent=has_leave_intent,
                        must_answer=must_answer,
                        language=face_lang,
                        topic_band=effective_topic_band,
                    )
                    if not quality_diag_is_hard(diag_prev):
                        final_response = prev_draft
                        last_diag = diag_prev
                        quality_ban_codes = list(diag_prev.get("ban_codes") or []) or quality_ban_codes
                    else:
                        # 再救一次：放宽 must 压迫，优先完整成句
                        mini2 = _mini_regen_reply(
                            profile=profile,
                            face_lang=face_lang,
                            user_input=user_input or "",
                            must_answer="",
                            ban_summary=(diag_m.get("ban_summary") or ban or "")
                            + "; complete spoken reply, no fragments",
                            lang_force=lang_force,
                            affection=eff_aff,
                            max_tok=min(int(max_tok or 256), 400),
                        )
                        if mini2:
                            cand = humanize(_scrub_visible_reply(mini2), face_lang)
                            diag2 = diagnose_reply_violations(
                                cand,
                                user_input=user_input or "",
                                recent_assistant=recent_assistant or [],
                                has_leave_intent=has_leave_intent,
                                must_answer=must_answer,
                                language=face_lang,
                                topic_band=effective_topic_band,
                            )
                            if not quality_diag_is_hard(diag2):
                                final_response = cand
                                last_diag = diag2
                                quality_ban_codes = list(diag2.get("ban_codes") or []) or quality_ban_codes
                quality_still_bad = bool((last_diag or {}).get("need_rewrite"))
                quality_hard = quality_diag_is_hard(last_diag)
            else:
                quality_still_bad = True

    # 质量改写后再确保语种（防 rewrite 串语）；失败放行最新稿
    final_response, lang_failed_post = _ensure_reply_lang(final_response, phase="post")
    lang_fix_failed = bool(_lang_failed or lang_failed_post)

    final_response = finalize_reply_for_leave(final_response, has_leave_intent)

    try:
        logger.info(
            "dialogue_quality_metrics %s",
            json.dumps(
                {
                    "face_lang": face_lang,
                    "reply_lang_source": lang_resolved.get("source") or "",
                    "lang_fix_failed": lang_fix_failed,
                    "quality_need_rewrite": quality_need_rewrite,
                    "quality_ban_codes": quality_ban_codes,
                    "quality_rewrite_count": quality_rewrite_count,
                    "quality_still_bad": quality_still_bad,
                    "quality_fallback_used": quality_fallback_used,
                    "quality_full_rerun": quality_full_rerun,
                    "quality_mini_regen": quality_mini_regen,
                    "quality_hard": quality_hard,
                    "quality_rescue_skipped": quality_rescue_skipped,
                    "quality_soft_only_turn": soft_only_turn,
                    "must_answer_present": bool(must_answer),
                    "must_miss": must_miss_flag,
                    "has_leave_intent": bool(has_leave_intent),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        pass

    # 供关系卡忌用窗落库
    fps = []
    try:
        from services.dialogue_phase2 import extract_reply_fingerprints

        fps = extract_reply_fingerprints(final_response)
    except Exception:
        fps = []

    return {
        "response": final_response,
        "think_result": prep_json.get("think", ""),
        "mood": mood,
        "affection": new_affection,
        "picked_hook": (fps[0] if fps else ""),
        "new_facts": [],
        "new_summary": companion_state.get("summary", ""),
        "evolved_personality": "",
        "evolved_background": "",
        "evolved_speech_style": "",
        "reply_lang": face_lang,
        "reply_lang_override": new_sticky or "",
        "reply_lang_source": lang_resolved.get("source") or "",
        "lang_fix_failed": lang_fix_failed,
        "quality_need_rewrite": quality_need_rewrite,
        "quality_ban_codes": quality_ban_codes,
        "quality_rewrite_count": quality_rewrite_count,
        "quality_still_bad": quality_still_bad,
        "quality_fallback_used": quality_fallback_used,
        "quality_full_rerun": quality_full_rerun,
        "quality_mini_regen": quality_mini_regen,
        "quality_hard": quality_hard,
        "quality_rescue_skipped": quality_rescue_skipped,
        "quality_soft_only_turn": soft_only_turn,
        "must_answer_present": bool(must_answer),
        "must_miss": must_miss_flag,
        "has_leave_intent": bool(has_leave_intent),
        "memory_snapshot": {
            "user_input": user_input,
            "final_response": final_response,
            "profile": profile,
            "companion_state": {**companion_state, "mood": mood, "affection": new_affection},
            "language": ui_lang,
            "summary_due": summary_due,
            "extract_due": extract_due,
        },
    }


def run_memory_update(snapshot: dict) -> dict:
    """后台 Call-3：条件 extract + summary + persona evolve。"""
    if not snapshot:
        return {}

    pseudo_state = {
        "user_input": snapshot["user_input"],
        "final_response": snapshot["final_response"],
        "profile": snapshot["profile"],
        "state": snapshot["companion_state"],
        "language": snapshot.get("language", "zh"),
        "memory_text": "",
        "summary_due": snapshot.get("summary_due", False),
        "extract_due": snapshot.get("extract_due", True),
    }

    new_facts: list = []
    new_summary = snapshot["companion_state"].get("summary", "")
    evolved = {"evolved_personality": "", "evolved_background": "", "evolved_speech_style": ""}

    if snapshot.get("extract_due", True):
        facts_result = extract_facts_node(pseudo_state)  # type: ignore[arg-type]
        new_facts = facts_result.get("new_facts", [])

    if snapshot.get("summary_due", False):
        summary_result = summary_node(pseudo_state)  # type: ignore[arg-type]
        new_summary = summary_result.get("new_summary", new_summary)

    interval = int(os.getenv("PERSONA_EVOLVE_INTERVAL", "10"))
    turns = snapshot["companion_state"].get("turns", 0)
    if turns > 0 and turns % interval == 0:
        evolve_result = persona_evolve_node(pseudo_state)  # type: ignore[arg-type]
        evolved = {
            "evolved_personality": evolve_result.get("evolved_personality", ""),
            "evolved_background": evolve_result.get("evolved_background", ""),
            "evolved_speech_style": evolve_result.get("evolved_speech_style", ""),
        }

    return {
        "new_facts": new_facts,
        "new_summary": new_summary,
        **evolved,
    }
