"""Pipeline v2: 2 前台 LLM + 条件后台，Token/延迟优化。"""
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from services.agent import (
    _calculate_affection_delta,
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
from services.agent import get_llm
from services.llm.client import llm_invoke, resolve_max_tokens
from services.knowledge_base import knowledge_base

logger = logging.getLogger(__name__)

_PREPARE_JSON_HINT = """分析用户消息，严格输出 JSON（无 markdown 围栏）：
{"think":"内心分析≤150字","mood":"情绪词","affection_note":"亲密度变化描述","creative_hint":"回复创意提示"}"""


def _parse_prepare_json(text: str) -> dict:
    raw = strip_outer_markdown_fence(text).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    mood_m = re.search(r'"mood"\s*:\s*"([^"]+)"', raw)
    think_m = re.search(r'"think"\s*:\s*"([^"]+)"', raw)
    return {
        "think": think_m.group(1) if think_m else raw[:150],
        "mood": mood_m.group(1) if mood_m else "开心",
        "affection_note": "",
        "creative_hint": "",
    }


def _search_knowledge(user_input: str) -> str:
    try:
        kb_results = knowledge_base.search_entries(user_input, top_k=3)
        if kb_results:
            return "【知识库参考】\n" + "\n".join(
                f"- {r['title']}: {r['content'][:300]}" for r in kb_results
            ) + "\n"
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
) -> dict:
    """前台 2 次 LLM：prepare_reflect + respond。"""
    lang = language
    evolved = {
        "personality": companion_state.get("evolved_personality", ""),
        "background": companion_state.get("evolved_background", ""),
        "speech_style": companion_state.get("evolved_speech_style", ""),
    }
    turns = companion_state.get("turns", 0)
    if extract_due is True:
        extract_due = _should_extract(user_input, turns)

    kb_text = knowledge_text or _search_knowledge(user_input)
    core_prompt = build_system_prompt(
        profile, lang, evolved=evolved, user_gender=user_gender, turns=turns, tier="core"
    )
    time_info = f"\n【当前时间】{current_time}" if current_time else ""

    # Call-1: think + reflect 合并
    llm1 = get_llm(max_tokens=200)
    prep_prompt = f"""{core_prompt}{time_info}

【当前状态】情绪：{companion_state.get('mood', '开心')} | 亲密度：{companion_state.get('affection', 0)}

【记忆上下文（精简）】
{memory_text[:800]}

{kb_text}

用户说：{user_input}

{_PREPARE_JSON_HINT}"""
    prep_resp = llm_invoke(llm1, [SystemMessage(content=prep_prompt)], node="prepare_reflect", max_tokens=200)
    prep_json = _parse_prepare_json(llm_content_to_str(getattr(prep_resp, "content", "")))

    old_affection = companion_state.get("affection", 0)
    new_affection = max(0, min(100, old_affection + _calculate_affection_delta(old_affection)))
    mood = prep_json.get("mood") or companion_state.get("mood", "开心")

    # Call-2: respond（Full Prompt 仅 1 次）
    full_prompt = build_system_prompt(
        profile, lang, evolved=evolved, user_gender=user_gender, turns=turns, tier="full"
    )
    restriction_text = get_content_restriction(lang, new_affection)
    name = profile.get("name", "Companion")

    respond_prompt = f"""{full_prompt}{time_info}

【内心分析】{prep_json.get('think', '')}
【情绪】{mood} | 亲密度：{new_affection}
【创意提示】{prep_json.get('creative_hint', '')}

【记忆上下文】
{memory_text}

{kb_text}

【当前状态】情绪：{mood} | 亲密度：{new_affection}

用户刚刚说：{user_input}

{restriction_text}

现在以 {name} 的身份直接对话。要求：口语化、自然、含留存钩子；按亲密度控制长度。
【Reply shape】遵循 system prompt 中括号/分段规则。"""

    llm2 = get_llm(max_tokens=resolve_max_tokens(new_affection))
    resp2 = llm_invoke(
        llm2,
        [SystemMessage(content=respond_prompt)],
        node="respond_v2",
        affection=new_affection,
        max_tokens=resolve_max_tokens(new_affection),
    )
    raw = strip_outer_markdown_fence(llm_content_to_str(getattr(resp2, "content", ""))).strip()
    final_response = humanize(raw, lang)

    return {
        "response": final_response,
        "think_result": prep_json.get("think", ""),
        "mood": mood,
        "affection": new_affection,
        "new_facts": [],
        "new_summary": companion_state.get("summary", ""),
        "evolved_personality": "",
        "evolved_background": "",
        "evolved_speech_style": "",
        "memory_snapshot": {
            "user_input": user_input,
            "final_response": final_response,
            "profile": profile,
            "companion_state": {**companion_state, "mood": mood, "affection": new_affection},
            "language": lang,
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
