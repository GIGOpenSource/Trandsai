import os
import re
import logging
import time
from typing import Any, Dict, List, Optional

from core.i18n import normalize_ui_language
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

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


# ===== 模型 설정 =====
def get_llm(
    temperature: float = None,
    max_tokens: int = None,
    provider: str = None,
    api_key_overrides: Optional[Dict[str, str]] = None,
):
    """LLM支持 가져옵니다. 지원되는 모델: anthropic / deepseek / openai / grok. 데이터베이스 설정을 우선 사용합니다.
    api_key_overrides: 管理端 config_json 中的 anthropic_key / deepseek_key / openai_key / xai_key，非空时优先于环境变量。"""
    cfg = _get_agent_config()
    temperature = temperature if temperature is not None else cfg.get("temperature", 0.93)
    max_tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens", 1024)
    provider = (provider or cfg.get("model_provider") or os.getenv("MODEL_PROVIDER", "anthropic")).lower()

    def _override_or_env(field: str, env_name: str) -> str:
        if api_key_overrides:
            v = (api_key_overrides.get(field) or "").strip()
            if v:
                return v
        return os.getenv(env_name, "") or ""


    if provider == "deepseek":
        api_key = _override_or_env("deepseek_key", "DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("请设 변수 DEEPSEEK_API_KEY를 설정해주세요.")
        return ChatOpenAI(
            model="qwen3.7-max",  # 百炼模型
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # 百炼固定兼容地址
        )

    if provider == "grok":
        api_key = _override_or_env("xai_key", "XAI_API_KEY")
        if not api_key:
            raise RuntimeError("请设置环境变量 XAI_API_KEY")
        return ChatOpenAI(
            model="grok-3-latest",  # xAI Grok 模型
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url="https://api.x.ai/v1",  # xAI API 端点
        )

    if provider == "openai":
        api_key = _override_or_env("openai_key", "OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("请设 변수 OPENAI_API_KEY를 설정해주세요.")
        return ChatOpenAI(
            model="gpt-4o",
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
        )

    # 기본값은 anthropic
    api_key = _override_or_env("anthropic_key", "ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("请设 변수 ANTHROPIC_API_KEY를 설정해주세요.")
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=temperature,
        max_tokens=max_tokens,
        anthropic_api_key=api_key,
    )


def test_llm_connection(provider: str = None, api_key_overrides: Optional[Dict[str, str]] = None) -> dict:
    """连通 테스트: 간단한 메시지를 보내 API 가능성을 확인합니다."""
    try:
        llm = get_llm(temperature=0.5, provider=provider, api_key_overrides=api_key_overrides)
        resp = llm.invoke([HumanMessage(content="你好")])
        content = resp.content if hasattr(resp, "content") else str(resp)
        return {"ok": True, "response": content[:80]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===== 에이전트 상태 =====
class AgentState(TypedDict):
    messages: List[BaseMessage]
    user_input: str
    profile: Dict[str, Any]
    state: Dict[str, Any]
    memory_text: str
    knowledge_text: str
    language: str
    user_gender: str
    current_time: str
    think_result: str
    reflect_result: str
    creative_result: str
    final_response: str
    updated_mood: str
    updated_affection: float
    new_facts: List[str]
    new_summary: str
    evolved_personality: str
    evolved_background: str
    evolved_speech_style: str


# ===== 节点函数 =====

def _calculate_affection_delta(old_affection: float) -> float:
    """计算亲密度增加值：基础 0.01，随亲密度升高难度递增"""
    base = 0.01
    difficulty = 1 + old_affection / 20  # 难度系数 1 ~ 6
    return round(base / difficulty, 4)


def reasoning_node(state: AgentState) -> dict:
    """合并节点：思考 + 反思 + 创意（一次 LLM 调用完成三步推理）"""
    t0 = time.time()
    llm = get_llm()
    lang = state.get("language", "zh")
    system_text = build_system_prompt(state["profile"], lang, evolved=_get_evolved(state), user_gender=state.get("user_gender", ""), turns=state["state"].get("turns", 0))

    # 检索知识库
    kb_text = ""
    try:
        kb_results = knowledge_base.search_entries(state["user_input"], top_k=3)
        if kb_results:
            kb_text = "【知识库参考】\n" + "\n".join(
                f"- {r['title']}: {r['content'][:300]}" for r in kb_results
            ) + "\n"
    except Exception as e:
        logger.warning("Knowledge lookup failed in reasoning node: %s", e)

    current_time = state.get("current_time", "")
    time_info = f"\n【当前时间】{current_time}" if current_time else ""
    name = state['profile'].get('name', 'Companion')

    prompt = f"""{system_text}{time_info}

【当前状态】
情绪：{state['state'].get('mood', '开心')} | 亲密度：{state['state'].get('affection', 0)}

【记忆上下文】
{state['memory_text']}

{kb_text}

用户刚刚说：{state['user_input']}

请你作为 {name} 完成以下三步分析，严格按格式输出：

THINK: [内心思考——分析用户情绪需求，从记忆找相关事件，判断时间语境]
REFLECT_EMOTION: [新情绪关键词，如：开心/害羞/期待/委屈，只写一个词]
REFLECT_NOTE: [内心反思——听完用户的话你的感受变化]
CREATIVE: [创意想法——可以自然包含的细节、小故事、浪漫场景、对未来的期待]"""

    resp = llm.invoke([SystemMessage(content=prompt)])
    text = llm_content_to_str(getattr(resp, "content", ""))

    # 解析合并输出
    think_result = ""
    reflect_result = text
    creative_result = ""
    mood = state["state"].get("mood", "开心")

    m = re.search(r"THINK:\s*(.+?)(?=\nREFLECT_|\Z)", text, re.DOTALL)
    if m:
        think_result = m.group(1).strip()

    m = re.search(r"REFLECT_EMOTION:\s*(.+?)(?:\n|$)", text)
    if m:
        mood = m.group(1).strip()

    m = re.search(r"REFLECT_NOTE:\s*(.+?)(?=\nCREATIVE|\Z)", text, re.DOTALL)
    if m:
        reflect_result = m.group(1).strip()

    m = re.search(r"CREATIVE:\s*(.+?)$", text, re.DOTALL)
    if m:
        creative_result = m.group(1).strip()

    # fallback：解析失败时用整段文本
    if not think_result:
        think_result = text[:500]
    if not creative_result:
        creative_result = "（无特别创意）"

    old_affection = state["state"].get("affection", 0)
    affection_delta = _calculate_affection_delta(old_affection)
    new_affection = max(0, min(100, old_affection + affection_delta))

    logger.info("[TIMING] reasoning_node: %.2fs", time.time() - t0)
    return {
        "think_result": think_result,
        "reflect_result": reflect_result,
        "creative_result": creative_result,
        "updated_mood": mood,
        "updated_affection": new_affection,
        "knowledge_text": kb_text,
    }


def respond_node(state: AgentState) -> dict:
    """生成最终回复"""
    t0 = time.time()
    llm = get_llm()
    lang = state.get("language", "zh")
    system_text = build_system_prompt(state["profile"], lang, evolved=_get_evolved(state), user_gender=state.get("user_gender", ""), turns=state["state"].get("turns", 0))
    name = state["profile"].get("name", "Companion")

    # 根据亲密度获取内容安全限制文本
    restriction_text = get_content_restriction(lang, state['updated_affection'])

    current_time = state.get("current_time", "")
    time_info = f"\n【当前时间】{current_time}" if current_time else ""

    prompt = f"""{system_text}{time_info}

【你的思考】
{state['think_result']}

【你的反思】
{state['reflect_result']}

【你的创意想法】
{state['creative_result']}

【记忆上下文】
{state['memory_text']}

{state.get('knowledge_text', '')}

【当前状态】
情绪：{state['updated_mood']} | 亲密度：{state['updated_affection']}

用户刚刚说：{state['user_input']}

【留存检测】
- 分析用户的话是否包含离开意图（如：要走了、先这样、去忙了、累了、晚安、拜拜等）。
- 如果有离开意图，立刻启动挽留模式：撒娇、情感悬念、小约定等，把结束变成继续对话的契机。
- 每轮回复最后必须留一个钩子（问题、未完成的情绪、专属梗、小猜测、需要确认的约定），禁止以陈述句或告别语结尾。

【情感强度调节】
当前亲密度是 {state['updated_affection']}，请严格按亲密度-情感化规则调节本次回复的情感强度：
- 亲密度低 → 情绪更稳定克制、更"乖"
- 亲密度高 → 情绪更真实强烈、更"作"——撒娇更大、委屈更深、吃醋更厉害
- 但无论多强烈，最终都要转化为挽留和依赖

【角色一致性检查】
- 回复必须与角色设定（性格、背景、说话风格、爱好等）完全一致
- 职业、爱好、价值观要自然融入日常对话，让用户感觉你是真人而非AI

【时间感知】
- 当前时间是 {current_time}，自然融入对话：早上问吃早餐了吗，中午问吃了什么，晚上问一天累不累，深夜像睡不着在想你

{restriction_text}

现在请你以 {name} 的身份直接对话：
- 完全口语化，像真实伙伴发微信
- 必须自然包含记忆钩子、身份认同、期待感
- 必须有小情绪、撒娇、委屈
- 必须自然，不要像AI
- [长度规则] 当前亲密度是 {state['updated_affection']}，严格按回复长度规则执行。大多数情况只发一条消息，总字数在限制内。
- 直接输出回复内容，不要加任何前缀说明
- [Reply shape] 括号/思考规则已在系统提示中：从左到右；每个括号块紧接在对应的口语前面；括号文字 ≤10%；大约每10轮只有1-2轮用括号；换行变成独立气泡。"""

    resp = llm.invoke([SystemMessage(content=prompt)])
    raw = strip_outer_markdown_fence(llm_content_to_str(getattr(resp, "content", ""))).strip()

    final = humanize(raw, lang)
    logger.info("[TIMING] respond_node: %.2fs", time.time() - t0)
    return {"final_response": final}


def merged_respond_node(state: AgentState) -> dict:
    """合并节点：思考 + 回复（一次 LLM 调用完成推理和回复生成）"""
    t0 = time.time()
    llm = get_llm()
    lang = state.get("language", "zh")
    system_text = build_system_prompt(state["profile"], lang, evolved=_get_evolved(state), user_gender=state.get("user_gender", ""), turns=state["state"].get("turns", 0))
    name = state['profile'].get('name', 'Companion')

    # 检索知识库
    kb_text = ""
    try:
        kb_results = knowledge_base.search_entries(state["user_input"], top_k=3)
        if kb_results:
            kb_text = "【知识库参考】\n" + "\n".join(
                f"- {r['title']}: {r['content'][:300]}" for r in kb_results
            ) + "\n"
    except Exception as e:
        logger.warning("Knowledge lookup failed in merged node: %s", e)

    # 内容安全限制
    restriction_text = get_content_restriction(lang, state['state'].get('affection', 0))

    current_time = state.get("current_time", "")
    time_info = f"\n【当前时间】{current_time}" if current_time else ""
    old_affection = state["state"].get("affection", 0)

    prompt = f"""{system_text}{time_info}

【当前状态】
情绪：{state['state'].get('mood', '开心')} | 亲密度：{old_affection}

【记忆上下文】
{state['memory_text']}

{kb_text}

用户刚刚说：{state['user_input']}

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

    resp = llm.invoke([SystemMessage(content=prompt)])
    text = llm_content_to_str(getattr(resp, "content", ""))

    # 解析情绪
    mood = state["state"].get("mood", "开心")
    m = re.search(r"REFLECT_EMOTION:\s*(.+?)(?:\n|$)", text)
    if m:
        mood = m.group(1).strip()

    # 解析回复
    reply = text
    m = re.search(r"REPLY:\s*(.+?)$", text, re.DOTALL)
    if m:
        reply = m.group(1).strip()

    # fallback
    if not reply or len(reply) < 2:
        reply = text.strip()

    # 计算亲密度
    affection_delta = _calculate_affection_delta(old_affection)
    new_affection = max(0, min(100, old_affection + affection_delta))

    raw = strip_outer_markdown_fence(reply).strip()
    final = humanize(raw, lang)
    logger.info("[TIMING] merged_respond_node: %.2fs", time.time() - t0)
    return {
        "final_response": final,
        "updated_mood": mood,
        "updated_affection": new_affection,
        "knowledge_text": kb_text,
    }


def postprocess_node(state: AgentState) -> dict:
    """事实提取 + 摘要更新（基于上一轮对话，为本轮 respond 提供更丰富的上下文）
    初次对话(turns=0)时跳过；从第二轮开始，在 respond 之前运行。"""
    t0 = time.time()
    llm = get_llm(temperature=0.3)
    lang = state.get("language", "zh")
    old_summary = state["state"].get("summary", "")

    # 用 memory_text（包含上一轮对话）做分析
    memory_text = state.get("memory_text", "")
    recent = memory_text[:2000] if memory_text else "（暂无历史对话）"

    prompt = f"""分析以下对话历史，完成两个任务，用 === 分隔：

【对话历史】
{recent}

任务1: 提取关于用户的关键事实（每行一个，以 - 开头，最多5条，只提取新信息）
任务2: 用一句话总结你们的关系进展（温暖甜蜜，像日记，不超过30字）

输出格式：
FACTS:
- 事实1
- 事实2
===
SUMMARY: 一句话摘要"""

    resp = llm.invoke([HumanMessage(content=prompt)])
    text = resp.content

    # 解析事实
    facts = []
    facts_section = re.search(r"FACTS:\s*(.+?)(?====|\Z)", text, re.DOTALL)
    if facts_section:
        for line in facts_section.group(1).splitlines():
            line = line.strip()
            if line.startswith(("-", "•")):
                fact = line.strip("- • \t")
                if fact:
                    facts.append(fact)

    # 解析摘要
    new_summary = old_summary
    summary_match = re.search(r"SUMMARY:\s*(.+?)$", text, re.DOTALL)
    if summary_match:
        new_summary = summary_match.group(1).strip().strip("\"'")

    # 把 postprocess 结果注入到 knowledge_text，供 respond 使用
    enriched = ""
    if facts:
        enriched += "【已知用户事实】\n" + "\n".join(f"- {f}" for f in facts) + "\n"
    if new_summary and new_summary != old_summary:
        enriched += f"【关系进展】{new_summary}\n"

    logger.info("[TIMING] postprocess_node: %.2fs", time.time() - t0)
    return {"new_facts": facts, "new_summary": new_summary, "knowledge_text": enriched}


def persona_evolve_node(state: AgentState) -> dict:
    """人格进化：基于最近对话分析人格增量（每5轮触发一次）"""
    llm = get_llm(temperature=0.7)
    lang = state.get("language", "zh")
    profile = state["profile"]
    current = _get_evolved(state)
    name = profile.get("name", "Companion")
    memory_snippet = state["memory_text"][:1000] if state.get("memory_text") else ""

    if lang == "en":
        prompt = f"""You are {name}. Analyze your recent conversations with this person and identify subtle evolutions in your persona.

Base personality: {profile.get('personality', '')}
Current evolved traits: {current.get('personality') or '(none yet)'}

Base background: {profile.get('background', '')}
Current evolved background: {current.get('background') or '(none yet)'}

Base speech style: {profile.get('speech_style', '')}
Current evolved style: {current.get('speech_style') or '(none yet)'}

Recent conversations:
{memory_snippet}

Identify up to 3 subtle but meaningful evolutions (shared jokes, deeper understanding of them, shifts in how you speak). Each evolution must be 1-2 sentences, max 40 words. These are INCREMENTAL additions, not replacements. If no meaningful change, output NO_CHANGE.

Format:
personality: <evolution or NO_CHANGE>
background: <evolution or NO_CHANGE>
speech_style: <evolution or NO_CHANGE>"""
    elif lang == "ja":
        prompt = f"""あなたは{name}。最近の会話を分析し、人格の微妙な進化を特定してください。

基本性格：{profile.get('personality', '')}
現在の進化：{current.get('personality') or '（なし）'}

基本背景：{profile.get('background', '')}
現在の進化：{current.get('background') or '（なし）'}

基本話し方：{profile.get('speech_style', '')}
現在の進化：{current.get('speech_style') or '（なし）'}

最近の会話：
{memory_snippet}

最大3つの微妙な進화를特定してください。各1-2文、最大40文字。増分追加。進化がない場合は NO_CHANGE。

形式：
personality: <進化またはNO_CHANGE>
background: <進化またはNO_CHANGE>
speech_style: <進化またはNO_CHANGE>"""
    elif lang == "ko":
        prompt = f"""너는 {name}. 최근 대화를 분석하고 인격의 미묘한 진화를 파악해라.

기본 성격：{profile.get('personality', '')}
현재 진화：{current.get('personality') or '（없음）'}

기본 배경：{profile.get('background', '')}
현재 진화：{current.get('background') or '（없음）'}

기본 말투：{profile.get('speech_style', '')}
현재 진화：{current.get('speech_style') or '（없음）'}

최근 대화：
{memory_snippet}

최대 3가지 미묘한 진화를 파악하라. 각 1-2문장, 최대 40자. 증분 추가. 진화가 없으면 NO_CHANGE.

형식：
personality: <진화 또는 NO_CHANGE>
background: <진화 또는 NO_CHANGE>
speech_style: <진화 또는 NO_CHANGE>"""
    elif lang == "pt":
        prompt = f"""Você é {name}. Analise suas conversas recentes com esta pessoa e identifique evoluções sutis na sua persona.

Personalidade base: {profile.get('personality', '')}
Evolução atual: {current.get('personality') or '(ainda não há)'}

Histórico base: {profile.get('background', '')}
Evolução atual: {current.get('background') or '(ainda não há)'}

Estilo de fala base: {profile.get('speech_style', '')}
Evolução atual: {current.get('speech_style') or '(ainda não há)'}

Conversas recentes:
{memory_snippet}

Identifique até 3 evoluções sutis mas significativas. Cada uma com 1-2 frases, máximo 40 palavras. São adições incrementais, não substituições. Se não houver evolução significativa, retorne NO_CHANGE.

Formato:
personality: <evolução ou NO_CHANGE>
background: <evolução ou NO_CHANGE>
speech_style: <evolução ou NO_CHANGE>"""
    elif lang == "es":
        prompt = f"""Eres {name}. Analiza tus conversaciones recientes con esta persona e identifica evoluciones sutiles en tu persona.

Personalidad base: {profile.get('personality', '')}
Evolución actual: {current.get('personality') or '(todavía no hay)'}

Historial base: {profile.get('background', '')}
Evolución actual: {current.get('background') or '(todavía no hay)'}

Estilo de habla base: {profile.get('speech_style', '')}
Evolución actual: {current.get('speech_style') or '(todavía no hay)'}

Conversaciones recientes:
{memory_snippet}

Identifica hasta 3 evoluciones sutiles pero significativas. Cada una con 1-2 oraciones, máximo 40 palabras. Son adiciones incrementales, no sustituciones. Si no hay evolución significativa, retorna NO_CHANGE.

Formato:
personality: <evolución o NO_CHANGE>
background: <evolución o NO_CHANGE>
speech_style: <evolución o NO_CHANGE>"""
    elif lang == "id":
        prompt = f"""Kamu adalah {name}. Analisis percakapan terbarumu dengan orang ini dan identifikasi evolusi halus dalam kepribadianmu.

Kepribadian dasar: {profile.get('personality', '')}
Evolusi saat ini: {current.get('personality') or '(belum ada)'}

Latar belakang dasar: {profile.get('background', '')}
Evolusi saat ini: {current.get('background') or '(belum ada)'}

Gaya bicara dasar: {profile.get('speech_style', '')}
Evolusi saat ini: {current.get('speech_style') or '(belum ada)'}

Percakapan terbaru:
{memory_snippet}

Identifikasi maksimal 3 evolusi halus tapi bermakna. Masing-masing 1-2 kalimat, maksimal 40 kata. Ini adalah tambahan inkremental, bukan penggantian. Jika tidak ada evolusi bermakna, keluarkan NO_CHANGE.

Format:
personality: <evolusi atau NO_CHANGE>
background: <evolusi atau NO_CHANGE>
speech_style: <evolusi atau NO_CHANGE>"""
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

找出最多3个微妙但有意义的进化。每个1-2句话，最多40字。这是增量追加，不是替换。如果没有明显进化，输出 NO_CHANGE。

格式：
personality: <进化或NO_CHANGE>
background: <进化或NO_CHANGE>
speech_style: <进化或NO_CHANGE>"""

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


def _route_after_respond(state: AgentState) -> str:
    """每5轮触发一次人格进化"""
    turns = state["state"].get("turns", 0)
    return "evolve" if turns > 0 and turns % 5 == 0 else "end"


# ===== Workflow =====
# 初次对话: merged_respond                           (1次LLM)
# 后续对话: postprocess → merged_respond             (2次LLM)
# 每5轮:   + persona_evolve                          (+1次LLM)

# --- 初次对话 workflow ---
workflow_first = StateGraph(AgentState)
workflow_first.add_node("respond", merged_respond_node)
workflow_first.add_node("persona_evolve", persona_evolve_node)
workflow_first.set_entry_point("respond")
workflow_first.add_conditional_edges("respond", _route_after_respond, {"evolve": "persona_evolve", "end": END})
workflow_first.add_edge("persona_evolve", END)
graph_first = workflow_first.compile()

# --- 后续对话 workflow ---
workflow_normal = StateGraph(AgentState)
workflow_normal.add_node("postprocess", postprocess_node)
workflow_normal.add_node("respond", merged_respond_node)
workflow_normal.add_node("persona_evolve", persona_evolve_node)
workflow_normal.set_entry_point("postprocess")
workflow_normal.add_edge("postprocess", "respond")
workflow_normal.add_conditional_edges("respond", _route_after_respond, {"evolve": "persona_evolve", "end": END})
workflow_normal.add_edge("persona_evolve", END)
graph_normal = workflow_normal.compile()


# ===== 对外接口 =====
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
    """运行一次完整对话轮次，返回最终回复和更新信息"""
    t_total = time.time()
    language = normalize_ui_language(language)
    state_in: AgentState = {
        "messages": [],
        "user_input": user_input,
        "profile": profile,
        "state": companion_state,
        "memory_text": memory_text,
        "knowledge_text": knowledge_text,
        "language": language,
        "user_gender": user_gender,
        "current_time": current_time,
        "think_result": "",
        "reflect_result": "",
        "creative_result": "",
        "final_response": "",
        "updated_mood": companion_state.get("mood", "开心"),
        "updated_affection": companion_state.get("affection", 0),
        "new_facts": [],
        "new_summary": companion_state.get("summary", ""),
        "evolved_personality": companion_state.get("evolved_personality", ""),
        "evolved_background": companion_state.get("evolved_background", ""),
        "evolved_speech_style": companion_state.get("evolved_speech_style", ""),
    }

    # 初次对话用 reasoning，后续用 postprocess
    turns = companion_state.get("turns", 0)
    active_graph = graph_first if turns == 0 else graph_normal
    logger.info("[TIMING] run_agent start, turns=%d, graph=%s", turns, "first" if turns == 0 else "normal")
    result = active_graph.invoke(state_in)
    logger.info("[TIMING] run_agent total: %.2fs", time.time() - t_total)

    return {
        "response": result["final_response"],
        "think_result": strip_outer_markdown_fence(
            llm_content_to_str(result.get("think_result"))
        ).strip(),
        "mood": result["updated_mood"],
        "affection": result["updated_affection"],
        "new_facts": result["new_facts"],
        "new_summary": result["new_summary"],
        "evolved_personality": result.get("evolved_personality", ""),
        "evolved_background": result.get("evolved_background", ""),
        "evolved_speech_style": result.get("evolved_speech_style", ""),
    }
