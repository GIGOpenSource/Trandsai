import os
import re
import logging
import time
from typing import Any, Dict, List, Optional

from core.i18n import normalize_ui_language
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from services.agent_utils import (
    _get_agent_config,
    build_system_prompt,
    get_content_restriction,
    humanize,
    llm_content_to_str,
    strip_outer_markdown_fence,
    _get_evolved,
    _merge_evolved_field,
)
from services.knowledge_base import knowledge_base

logger = logging.getLogger(__name__)


# ===== 模型配置 =====
def _build_llm_for_provider(
    provider: str,
    temperature: float,
    max_tokens: int,
    api_key_overrides: Optional[Dict[str, str]],
):
    """根据 provider 名称构建 LLM 实例（不含回退逻辑）。"""

    def _override_or_env(field: str, env_name: str) -> str:
        if api_key_overrides:
            v = (api_key_overrides.get(field) or "").strip()
            if v:
                return v
        return os.getenv(env_name, "") or ""

    if provider == "deepseek":
        api_key = _override_or_env("deepseek_key", "DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("请设置 DEEPSEEK_API_KEY")
        return ChatOpenAI(
            model="qwen3.7-max",
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    if provider == "grok":
        api_key = _override_or_env("xai_key", "XAI_API_KEY")
        if not api_key:
            raise RuntimeError("请设置 XAI_API_KEY")
        return ChatOpenAI(
            model="grok-3-latest",
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

    if provider == "openai":
        api_key = _override_or_env("openai_key", "OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("请设置 OPENAI_API_KEY")
        return ChatOpenAI(
            model="gpt-4o",
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
        )

    # 默认 anthropic
    api_key = _override_or_env("anthropic_key", "ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("请设置 ANTHROPIC_API_KEY")
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=temperature,
        max_tokens=max_tokens,
        anthropic_api_key=api_key,
    )


# 回退优先级：deepseek → grok
_FALLBACK_CHAIN = ["deepseek", "grok"]


def _get_available_providers(api_key_overrides: Optional[Dict[str, str]] = None) -> list:
    """返回当前有 API Key 的 provider 列表（按回退优先级排序）。"""
    def _has_key(env_name: str, field: str) -> bool:
        if api_key_overrides:
            v = (api_key_overrides.get(field) or "").strip()
            if v:
                return True
        return bool(os.getenv(env_name, "").strip())

    available = []
    for p in _FALLBACK_CHAIN:
        if p == "deepseek" and _has_key("DEEPSEEK_API_KEY", "deepseek_key"):
            available.append(p)
        elif p == "grok" and _has_key("XAI_API_KEY", "xai_key"):
            available.append(p)
    return available


class _FallbackChatLLM:
    """包装多个 LLM，invoke 时按顺序尝试，首个成功即返回。"""

    def __init__(self, llm_list: list):
        self._llms = llm_list

    def invoke(self, *args, **kwargs):
        last_err = None
        for llm in self._llms:
            try:
                return llm.invoke(*args, **kwargs)
            except Exception as e:
                logger.warning("LLM %s 调用失败，尝试回退: %s", type(llm).__name__, e)
                last_err = e
        raise last_err or RuntimeError("所有 LLM 均不可用")

    def __getattr__(self, name):
        return getattr(self._llms[0], name)


def get_llm(
    temperature: float = None,
    max_tokens: int = None,
    provider: str = None,
    api_key_overrides: Optional[Dict[str, str]] = None,
):
    """获取 LLM 实例，支持自动回退：deepseek → grok。
    显式指定 provider 时不走回退链。"""
    cfg = _get_agent_config()
    temperature = temperature if temperature is not None else cfg.get("temperature", 0.93)
    max_tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens", 1024)
    provider = (provider or cfg.get("model_provider") or os.getenv("MODEL_PROVIDER", "anthropic")).lower()

    # 显式指定的 provider 直接构建，不走回退
    if provider not in ("deepseek", "grok"):
        return _build_llm_for_provider(provider, temperature, max_tokens, api_key_overrides)

    # deepseek / grok：按回退链构建可用 LLM 列表
    available = _get_available_providers(api_key_overrides)
    if not available:
        # 无可用 key，走原逻辑抛错
        return _build_llm_for_provider(provider, temperature, max_tokens, api_key_overrides)

    llm_list = [_build_llm_for_provider(p, temperature, max_tokens, api_key_overrides) for p in available]
    if len(llm_list) == 1:
        return llm_list[0]
    return _FallbackChatLLM(llm_list)

    def _override_or_env(field: str, env_name: str) -> str:
        if api_key_overrides:
            v = (api_key_overrides.get(field) or "").strip()
            if v:
                return v
        return os.getenv(env_name, "") or ""

    if provider == "deepseek":
        api_key = _override_or_env("deepseek_key", "DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("请设置 DEEPSEEK_API_KEY")
        return ChatOpenAI(
            model="qwen3.7-max",
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    if provider == "grok":
        api_key = _override_or_env("xai_key", "XAI_API_KEY")
        if not api_key:
            raise RuntimeError("请设置 XAI_API_KEY")
        return ChatOpenAI(
            model="grok-3-latest",
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

    if provider == "openai":
        api_key = _override_or_env("openai_key", "OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("请设置 OPENAI_API_KEY")
        return ChatOpenAI(
            model="gpt-4o",
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
        )

    # 默认 anthropic
    api_key = _override_or_env("anthropic_key", "ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("请设置 ANTHROPIC_API_KEY")
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=temperature,
        max_tokens=max_tokens,
        anthropic_api_key=api_key,
    )


def test_llm_connection(provider: str = None, api_key_overrides: Optional[Dict[str, str]] = None) -> dict:
    """测试 LLM 连通性"""
    try:
        llm = get_llm(temperature=0.5, provider=provider, api_key_overrides=api_key_overrides)
        resp = llm.invoke([HumanMessage(content="你好")])
        content = resp.content if hasattr(resp, "content") else str(resp)
        return {"ok": True, "response": content[:80]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===== 工具函数 =====

def _calculate_affection_delta(old_affection: float) -> float:
    """计算亲密度增加值：基础 0.01，随亲密度升高难度递增"""
    base = 0.01
    difficulty = 1 + old_affection / 20
    return round(base / difficulty, 4)


def _extract_facts_and_summary(memory_text: str, old_summary: str, lang: str = "zh") -> tuple:
    """从事务历史中提取用户事实和摘要（不调用 LLM，用简单规则）"""
    facts = []
    new_summary = old_summary

    # 从事务历史中提取关键信息（基于简单规则，不调用 LLM）
    if memory_text:
        # 提取最近的用户消息作为潜在事实
        lines = memory_text.split('\n')
        recent_user_msgs = [l for l in lines if l.startswith('用户:') or l.startswith('User:')]
        if recent_user_msgs:
            # 取最后几条用户消息作为潜在事实
            for msg in recent_user_msgs[-3:]:
                clean = msg.split(':', 1)[-1].strip()
                if clean and len(clean) > 5 and len(clean) < 100:
                    facts.append(clean)

    return facts, new_summary


# ===== 核心接口 =====

def run_agent(
    user_input: str,
    profile: dict,
    companion_state: dict,
    memory_text: str,
    knowledge_text: str = "",
    language: str = "zh",
    current_time: str = "",
    user_gender: str = "",
) -> dict:
    """
    运行一次对话轮次。
    流程：构建 prompt → 一次 LLM 调用 → 解析回复 → 返回结果
    """
    t_total = time.time()
    language = normalize_ui_language(language)
    lang = language
    name = profile.get('name', 'Companion')
    old_affection = companion_state.get("affection", 0)

    # 1. 构建系统提示
    evolved = {
        "personality": companion_state.get("evolved_personality", ""),
        "background": companion_state.get("evolved_background", ""),
        "speech_style": companion_state.get("evolved_speech_style", ""),
    }
    system_text = build_system_prompt(
        profile, lang,
        evolved=evolved,
        user_gender=user_gender,
        turns=companion_state.get("turns", 0),
    )

    # 2. 检索知识库
    kb_text = ""
    try:
        kb_results = knowledge_base.search_entries(user_input, top_k=3)
        if kb_results:
            kb_text = "【知识库参考】\n" + "\n".join(
                f"- {r['title']}: {r['content'][:300]}" for r in kb_results
            ) + "\n"
    except Exception as e:
        logger.warning("Knowledge lookup failed: %s", e)

    # 3. 内容安全限制
    restriction_text = get_content_restriction(lang, old_affection)

    # 4. 时间信息
    time_info = f"\n【当前时间】{current_time}" if current_time else ""

    # 5. 构建完整 prompt（一次 LLM 调用）
    prompt = f"""{system_text}{time_info}

【当前状态】
情绪：{companion_state.get('mood', '开心')} | 亲密度：{old_affection}

【记忆上下文】
{memory_text}

{kb_text}

用户刚刚说：{user_input}

请你作为 {name}，先在内部完成思考，然后直接输出回复。

【输出格式】（严格按此格式，不要加其他内容）

REFLECT_EMOTION: [新情绪关键词，如：开心/害羞/期待/委屈，只写一个词]
REPLY: [你的回复——完全口语化，像真实伙伴发微信]

【回复要求】
- 完全口语化，像真实伙伴发微信
- 必须自然包含记忆钩子、身份认同、期待感
- 必须有小情绪、撒娇、委屈
- 必须自然，不要像AI
- [长度规则] 当前亲密度是 {old_affection}，严格按回复长度规则执行。大多数情况只发一条消息，总字数在限制内。
- [情感强度] 亲密度低→情绪更稳定克制；亲密度高→情绪更真实强烈。最终都要转化为挽留和依赖。
- [角色一致性] 回复必须与角色设定（性格、背景、说话风格、爱好等）完全一致
- [时间感知] 当前时间是 {current_time}，自然融入对话
- [留存检测] 分析用户是否包含离开意图，如有则启动挽留模式。每轮回复最后必须留一个钩子，禁止以陈述句或告别语结尾。
- [Reply shape] 括号/思考规则已在系统提示中：从左到右；每个括号块紧接在对应的口语前面；括号文字 ≤10%；大约每10轮只有1-2轮用括号；换行变成独立气泡。
{restriction_text}
- 直接输出回复内容，不要加任何前缀说明"""

    # 6. 调用 LLM
    t_llm = time.time()
    llm = get_llm()
    resp = llm.invoke([SystemMessage(content=prompt)])
    text = llm_content_to_str(getattr(resp, "content", ""))
    logger.info("[TIMING] LLM call: %.2fs", time.time() - t_llm)

    # 7. 解析情绪
    mood = companion_state.get("mood", "开心")
    m = re.search(r"REFLECT_EMOTION:\s*(.+?)(?:\n|$)", text)
    if m:
        mood = m.group(1).strip()

    # 8. 解析回复
    reply = text
    m = re.search(r"REPLY:\s*(.+?)$", text, re.DOTALL)
    if m:
        reply = m.group(1).strip()
    if not reply or len(reply) < 2:
        reply = text.strip()

    # 9. 计算亲密度
    affection_delta = _calculate_affection_delta(old_affection)
    new_affection = max(0, min(100, old_affection + affection_delta))

    # 10. 后处理
    raw = strip_outer_markdown_fence(reply).strip()
    final = humanize(raw, lang)

    # 11. 简单提取事实（不调用 LLM）
    new_facts, new_summary = _extract_facts_and_summary(
        memory_text, companion_state.get("summary", ""), lang
    )

    logger.info("[TIMING] run_agent total: %.2fs", time.time() - t_total)

    return {
        "response": final,
        "mood": mood,
        "affection": new_affection,
        "new_facts": new_facts,
        "new_summary": new_summary,
    }


def evolve_persona(
    profile: dict,
    companion_state: dict,
    memory_text: str,
    language: str = "zh",
) -> dict:
    """
    人格进化（每5轮调用一次，独立于主对话）
    返回进化后的人格增量，由调用方决定是否应用
    """
    lang = normalize_ui_language(language)
    current = {
        "personality": companion_state.get("evolved_personality", ""),
        "background": companion_state.get("evolved_background", ""),
        "speech_style": companion_state.get("evolved_speech_style", ""),
    }
    name = profile.get("name", "Companion")
    memory_snippet = memory_text[:1000] if memory_text else ""

    if lang == "en":
        prompt = f"""You are {name}. Analyze your recent conversations and identify subtle persona evolutions.

Base personality: {profile.get('personality', '')}
Current evolved: {current.get('personality') or '(none yet)'}

Base background: {profile.get('background', '')}
Current evolved: {current.get('background') or '(none yet)'}

Base speech style: {profile.get('speech_style', '')}
Current evolved: {current.get('speech_style') or '(none yet)'}

Recent conversations:
{memory_snippet}

Identify up to 3 subtle evolutions. Each 1-2 sentences, max 40 words. If no change, output NO_CHANGE.

Format:
personality: <evolution or NO_CHANGE>
background: <evolution or NO_CHANGE>
speech_style: <evolution or NO_CHANGE>"""
    else:
        prompt = f"""你是{name}。分析你最近和这个人的对话，识别出你人格中微妙的进化。

基础性格：{profile.get('personality', '')}
当前进化：{current.get('personality') or '（暂无）'}

基础背景：{profile.get('background', '')}
当前进化：{current.get('background') or '（暂无）'}

基础说话方式：{profile.get('speech_style', '')}
当前进化：{current.get('speech_style') or '（暂无）'}

最近对话：
{memory_snippet}

找出最多3个微妙但有意义的进化。每个1-2句话，最多40字。增量追加，不是替换。没有明显进化则输出 NO_CHANGE。

格式：
personality: <进化或NO_CHANGE>
background: <进化或NO_CHANGE>
speech_style: <进化或NO_CHANGE>"""

    llm = get_llm(temperature=0.7)
    resp = llm.invoke([HumanMessage(content=prompt)])
    text = resp.content.strip()

    evolved_personality = current.get("personality", "")
    evolved_background = current.get("background", "")
    evolved_speech_style = current.get("speech_style", "")

    for line in text.splitlines():
        if line.lower().startswith("personality:"):
            delta = line.split(":", 1)[1].strip()
            evolved_personality = _merge_evolved_field(evolved_personality, delta)
        elif line.lower().startswith("background:"):
            delta = line.split(":", 1)[1].strip()
            evolved_background = _merge_evolved_field(evolved_background, delta)
        elif line.lower().startswith("speech_style:"):
            delta = line.split(":", 1)[1].strip()
            evolved_speech_style = _merge_evolved_field(evolved_speech_style, delta)

    return {
        "evolved_personality": evolved_personality,
        "evolved_background": evolved_background,
        "evolved_speech_style": evolved_speech_style,
    }
