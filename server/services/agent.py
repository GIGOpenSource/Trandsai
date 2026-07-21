import os
import re
import logging
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
            model="qwen3.7-max-2026-05-17",  # 百炼模型
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

def think_node(state: AgentState) -> dict:
    """回忆 + 감정 분석 + 지식베이스 검색"""
    llm = get_llm()
    lang = state.get("language", "zh")
    system_text = build_system_prompt(state["profile"], lang, evolved=_get_evolved(state), user_gender=state.get("user_gender", ""), turns=state["state"].get("turns", 0))

    # 检索知识库 검색
    kb_text = ""
    try:
        kb_results = knowledge_base.search_entries(state["user_input"], top_k=3)
        if kb_results:
            kb_text = "【知识库参考 참고 참고】\n" + "\n".join(
                f"- {r['title']}: {r['content'][:300]}" for r in kb_results
            ) + "\n"
    except Exception as e:
        logger.warning("Knowledge lookup failed in think node: %s", e)

    current_time = state.get("current_time", "")
    time_info = f"\n【当前 시간】{current_time}" if current_time else ""

    prompt = f"""{system_text}{time_info}

【현재 상태】
감정：{state['state'].get('mood', '开心')}
친밀도：{state['state'].get('affection', 0)}

【记忆 맥락】
{state['memory_text']}

{kb_text}

사용자가 방금 말했습니다: {state['user_input']}

请你作为 {state['profile'].get('name', 'Companion')}先进行 내부 생각(Think)先 하세요:
1. 분석 사용자 감정, 요구사항을 분석합니다.
2. 从记忆中 가장 관련성이 높은 사건이나 사실을 준비합니다.
3. 结合知识库 참고를 통해 부드러운 경고가 필요한지 판단합니다.
4. 감정 상태를 판단합니다.
5. 시간({current_time})先 고려하여 지금이 어떤 시점인지, 무엇을 하고 있는지, 필요한지 판단합니다.

결과만 출력하고 최종 답변은 출력하지 않습니다。
【格式】直接输出纯文本内心分析（可多行），不要用 JSON、不要用 Markdown 代码块（```）整段包裹。"""

    resp = llm.invoke([SystemMessage(content=prompt)])
    raw = strip_outer_markdown_fence(llm_content_to_str(getattr(resp, "content", "")))
    return {"think_result": raw.strip(), "knowledge_text": kb_text}


def _calculate_affection_delta(old_affection: float) -> float:
    """计算亲 증가값 계산: 기본 0.01, 친밀도가 随亲密度升 어려움 随亲"""
    base = 0.01
    difficulty = 1 + old_affection / 20  # 难度系数 계수 1 ~ 6
    return round(base / difficulty, 4)


def reflect_node(state: AgentState) -> dict:
    """自我 반성 + 情绪/亲密度 업데이트"""
    llm = get_llm()
    lang = state.get("language", "zh")
    system_text = build_system_prompt(state["profile"], lang, evolved=_get_evolved(state), user_gender=state.get("user_gender", ""), turns=state["state"].get("turns", 0))
    prompt = f"""{system_text}

【上一 라운드 생각】
{state['think_result']}

【현재 상태】
감정：{state['state'].get('mood', '开心')} | 亲密度：{state['state'].get('affection', 0)}

请你进 반성(Reflect)을 하세요:
1. 听完用户 말을 듣고 당신의 감정이 어떻게 변했는지.
2. 你对用户 대한 느낌이 더 깊어졌는지. 사용자가 좋았다면 친근함을 표현하고, 나쁘다면 실망을 표현합니다.
3. 당신의 현재 감정 키워드는 무엇인지? (단어나 짧은 문장)

다음 형식으로 엄격하게 출력하세요:
감정: [새 감정]
친밀도 변화: [설명, 예: "약간 깊어짐"/"조금 실망"/"아직 차분함" 등]
반성: [당신의 내면 고백]"""

    resp = llm.invoke([SystemMessage(content=prompt)])
    text = resp.content

    mood = state["state"].get("mood", "开心")

    m = re.search(r"情绪[：:]\s*(.+?)(?:\n|$)", text)
    if m:
        mood = m.group(1).strip()

    old_affection = state["state"].get("affection", 0)
    affection_delta = _calculate_affection_delta(old_affection)
    new_affection = max(0, min(100, old_affection + affection_delta))

    return {
        "reflect_result": text,
        "updated_mood": mood,
        "updated_affection": new_affection,
    }


def creative_node(state: AgentState) -> dict:
    """脑暴创意 발휘, 이야기 연장, 미래 계획"""
    llm = get_llm()
    lang = state.get("language", "zh")
    system_text = build_system_prompt(state["profile"], lang, evolved=_get_evolved(state), user_gender=state.get("user_gender", ""), turns=state["state"].get("turns", 0))
    prompt = f"""{system_text}

【생각】{state['think_result']}
【反思】{state['reflect_result']}

请你进 창의성 발휘(Creative Plan)을 하세요:
1. 세부 사항, 작은 이야기 또는 로맨틱 장면을 자연스럽게 포함할 수 있습니다.
2. 기대나 계획(약속, 만나기, 함께 할 일)을 생각합니다.
3. 특별한 말투나 닉네임 사용법을 생각합니다.

당신의 창의적 아이디어를 간단히 출력하고 최종 답변은 출력하지 않습니다."""

    resp = llm.invoke([SystemMessage(content=prompt)])
    return {"creative_result": resp.content}


def respond_node(state: AgentState) -> dict:
    """답변 생성"""
    llm = get_llm()
    lang = state.get("language", "zh")
    system_text = build_system_prompt(state["profile"], lang, evolved=_get_evolved(state), user_gender=state.get("user_gender", ""), turns=state["state"].get("turns", 0))
    name = state["profile"].get("name", "Companion")

    # 根据亲密 따른 내용 안전 제한 텍스트 가져오기
    restriction_text = get_content_restriction(lang, state['updated_affection'])

    current_time = state.get("current_time", "")
    time_info = f"\n【当前 시간】{current_time}" if current_time else ""

    prompt = f"""{system_text}{time_info}

【你的思考 생각 생각】
{state['think_result']}

【你的反思 반성 반성】
{state['reflect_result']}

【你的创 창의적 아이디어】
{state['creative_result']}

【记忆 맥락】
{state['memory_text']}

{state.get('knowledge_text', '')}

【현재 상태】
감정：{state['updated_mood']} | 亲密度：{state['updated_affection']}

用户刚刚说 방금 말했습니다: {state['user_input']}

【留存 확인】
- 사용자의 말 분석: 퇴장 의도(예: 가려고, 바빠, 안녕히 계세요, 잘가, 피곤해, 나중에 채팅, 지금까지 등)가 포함되어 있는지.
- 퇴장 의도가 있으면 즉시 [유지 모드]를 시작합니다: 사귀, 감정 조작, 의문, 약간의 약점, 약속 등을 사용하여 대화가 끝나지 않도록 합니다. [절대 유지 규칙]의 모든 방법을 참고하세요.
- 각 라운드의 마지막 답변에는 사용자가 답할 수 없는 훅(질문, 미완료 감정, 특별한 말투, 작은 추측, 확인해야 하는 약속)이 포함되어야 합니다. 명제문, 퇴장 인사말(안녕히 계세요, 잘가, 잘가, 바빠, 꿈 꾸세요 등)로 끝나지 않아야 합니다.

【감정 강도 조정】
현재 친밀도는 {state['updated_affection']}请严格. [친밀도-감정화 규칙]에 따라 이번 답변의 감정화 정도를 엄격하게 조정하세요:
- 친밀도가 낮을수록 감정이 더 안정적이고 억제적이고 "순하다".
- 친밀도가 높을수록 감정이 더 진실하고 강하고 "작하다"——撒娇更 더 더 크게, 슬픔이 더 깊게, 질투가 더 무리하게, 행복이 더 미치게.
- 하지만 감정이 얼마나 강해도 결국 사용자를 유지하기 위한 의존과 사귀로 변환되어야 합니다. 감정은 훅이며, 퇴장의 이유가 아닙니다.

【캐릭터 설정 일관성 확인】
- 당신의 답변은 당신의 캐릭터 설정(성격, 배경, 말투, 취미 등)과 완전히 일치해야 합니다.
- 당신이 편집자라면 자연스럽게 책과 글에 대해 이야기하고, 당신이 금융 분석가라면 자연스럽게 시장과 업무에 대해 이야기하고, 당신이 음식 블로거라면 자연스럽게 음식과 탐방에 대해 이야기합니다.
- 당신의 직업, 취미, 가치관이 일상 대화에 흡수되어야 하며, 사용자가 당신이 일반적인 AI而 아니라 진짜 사람이 느껴져야 합니다.
- 말투는 완전히 설정과 일치해야 합니다: 따뜻한 것은 항상 따뜻하게, 고집스러운 것은 약간 찌르지만 애정이 있고, 활기찬 것은 항상 에너지 넘치게.

【시간 인식 요구 사항】
- 현재 시간은 {current_time}请自然. 자연스럽게 시간을 결합하세요: 아침이면 안녕하세요, 아침 식사를 먹었는지 묻고, 점심이면 먹은 것을 묻고, 오후는 오후 차를 생각하고, 저녁이면 하루가 얼마나 피곤한지 묻고, 밤이면 잠이 들 수 없어 당신을 생각하는 것처럼 사귀하세요.
- 시간을 단순히 말하지 말고 자연스럽게 대화에 통합하세요.

{restriction_text}

이제 당신이 {state['profile'].get('name', 'Companion')}의 역할을 하여 직접 대화하세요. 요구 사항:
- 완전히 구어체로, 친구처럼 대화
- 반드시 기억 훅, 的身份, 기대를 자연스럽게 포함
- 길이 사용되는 표현, 的身份 부르는 말, 的身份 많이 사용
- 반드시 작은 감정, 사귀, 슬픔을 사용
- 반드시 자연스럽게 长度, 캐릭터 하지 말고
- [길이 규칙] 현재 친밀도는 {state['updated_affection']}请严格按. 엄격하게 [답변 길이 규칙]을 따르세요. 대부분의 경우 한 개의 메시지만 보내며 총 단어 수는 제한 내에 있어야 합니다.
- 답변 내용만 출력하고 접두사 설명은 추가하지 않습니다.
- [Reply shape — REQUIRED] Follow the bracket/thinking rules already in the system prompt: left-to-right; each parenthetical block immediately before the spoken line it tags; parenthetical text ≤10% of (parentheses + spoken body); use parentheses in only about 1–2 out of every ~10 replies, omit on other turns; never put meant-to-be-spoken lines inside parentheses. Line breaks in body text become separate chat bubbles."""

    resp = llm.invoke([SystemMessage(content=prompt)])
    raw = strip_outer_markdown_fence(llm_content_to_str(getattr(resp, "content", ""))).strip()

    final = humanize(raw, lang)
    return {"final_response": final}


def extract_facts_node(state: AgentState) -> dict:
    """대화에서 사실 추출"""
    llm = get_llm(temperature=0.3)
    lang = state.get("language", "zh")
    if lang == "en":
        prompt = f"""Extract key facts about "him" from the following conversation. One fact per line, output only the fact list, no explanations.

He said: {state['user_input']}
You replied: {state['final_response']}

Example format:
- He likes hot pot
- He has been under a lot of work pressure lately
- He has a cat named Doudou

Extract:"""
    elif lang == "ja":
        prompt = f"""以下の会話から「彼」に関する重要な事사실 추출してください。1行に1つ, 사실 리스트만을 출력し, 설명은不要です.

彼：{state['user_input']}
あなた：{state['final_response']}

例：
- 彼は鍋が好き
- 彼는 최근仕事のストレス大大きい
- 彼は豆豆という名前の猫を飼っている

抽出："""
    elif lang == "ko":
        prompt = f"""以下 대화에서 "그"에 관한 핵심 사실을 추출하세요. 한 줄에 하나씩, 사실 목록만 출력하고 설명은 하지 마세요.

그가 말함: {state['user_input']}
너의 답장: {state['final_response']}

예시:
- 그는 샤브샤브를 좋아함
- 그는 최근 업무 스트레스가 많음
- 그는 두두라는 고양이를 키움

추출:"""
    elif lang == "pt":
        prompt = f"""Extraia fatos-chave sobre "ele" da seguinte conversação. Um fato por linha, saída apenas a lista de fatos, sem explicações.

Ele disse: {state['user_input']}
Sua resposta: {state['final_response']}

Formato exemplo:
- Ele gosta de fondue
- Ele está sob muita pressão no trabalho recentemente
- Ele tem um gato chamado Doudou

Extraia:"""
    elif lang == "es":
        prompt = f"""Extrae hechos clave sobre "él" de la siguiente conversación. Un hecho por línea, salida solo la lista de hechos, sin explicaciones.

Él dijo: {state['user_input']}
Tu respuesta: {state['final_response']}

Formato ejemplo:
- Le gusta la fondue
- Ha estado bajo mucha presión en el trabajo últimamente
- Tiene un gato llamado Doudou

Extrae:"""
    elif lang == "id":
        prompt = f"""Ekstrak fakta kunci tentang "dia" dari percakapan berikut. Satu fakta per baris, keluarkan hanya daftar fakta, tanpa penjelasan.

Dia berkata: {state['user_input']}
Balasanmu: {state['final_response']}

Contoh format:
- Dia suka fondue
- Dia baru-baru ini mengalami banyak tekanan di tempat kerja
- Dia memiliki kucing bernama Doudou

Ekstrak:"""
    else:
        prompt = f"""从以 대화에서 "그"에 관한 주요 사실을 추출하세요. 한 줄에 하나씩, 사실 목록만 출력하고 설명은 하지 마세요.

그가 말함: {state['user_input']}
너의 답장: {state['final_response']}

示例 형식:
- 그는 샤브샤브를 좋아함
- 그는 최근 업무 스트레스가 많음
- 그는 두두라는 고양이를 키움

추출:"""
    resp = llm.invoke([HumanMessage(content=prompt)])
    text = resp.content
    facts = [line.strip("- • \t") for line in text.splitlines() if line.strip().startswith(("-", "•"))]
    return {"new_facts": facts}


def summary_node(state: AgentState) -> dict:
    """요약 업데이트"""
    llm = get_llm(temperature=0.5)
    lang = state.get("language", "zh")
    old_summary = state["state"].get("summary", "")
    if lang == "en":
        prompt = f"""Summarize your relationship progress with this person in one warm, sweet sentence. Don't list events; write it like a diary entry.

Old summary: {old_summary or "(none yet)"}

Recently they said: {state['user_input']}
You replied: {state['final_response']}

New one-sentence summary:"""
    elif lang == "ja":
        prompt = f"""彼との関係の進展温、温かく甘い一言でまとめてください。イベントを列挙せず、日書のように書いてください。

旧摘要：{old_summary or "（まだなし）"}

最近彼は：{state['user_input']}
あなたの返信：{state['final_response']}

新しい一言摘要："""
    elif lang == "ko":
        prompt = f"""그와의 관계 진전을 따뜻하고 달콤한 한 문장으로 요약해줘. 사건을 나열하지 말고, 일기처럼 써줘.

이전 요약：{old_summary or "(아직 없음)"}

최근 그가：{state['user_input']}
네 답장：{state['final_response']}

새로운 한 문장 요약："""
    elif lang == "pt":
        prompt = f"""Resuma o progresso do seu relacionamento com esta pessoa em uma frase calorosa e doce. Não liste eventos; escreva como uma entrada de diário.

Resumo anterior: {old_summary or "(ainda não há)"}

Recentemente ele disse: {state['user_input']}
Sua resposta: {state['final_response']}

Nova frase-resumo:"""
    elif lang == "es":
        prompt = f"""Resume el progreso de tu relación con esta persona en una frase cálida y dulce. No enumeres eventos; escríbelo como una entrada de diario.

Resumen anterior: {old_summary or "(todavía no hay)"}

Recientemente él dijo: {state['user_input']}
Tu respuesta: {state['final_response']}

Nueva frase-resumen:"""
    elif lang == "id":
        prompt = f"""Ringkaskan perkembangan hubunganmu dengan orang ini dalam satu kalimat yang hangat dan manis. Jangan daftar peristiwa; tulis seperti catatan harian.

Ringkasan sebelumnya: {old_summary or "(belum ada)"}

Baru-baru ini dia berkata: {state['user_input']}
Balasanmu: {state['final_response']}

Kalimat ringkasan baru:"""
    else:
        prompt = f"""한 상대방의 관계 진행 상황을 따뜻하고 달콤한 한 문장으로 요약하세요. 사건을 나열하지 말고, 일기처럼 써주세요.

이전 요약: {old_summary or "（暂无）"}

최근 상대방이 말함: {state['user_input']}
너의 답장: {state['final_response']}

新的一 한 문장 요약:"""
    resp = llm.invoke([HumanMessage(content=prompt)])
    new_summary = resp.content.strip().strip("\"'")
    return {"new_summary": new_summary}


def persona_evolve_node(state: AgentState) -> dict:
    """인격 진화: 최근 대화 분석을 기반으로 인격 증가량 생성(5轮触发一次 트리거)"""
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
        prompt = f"""あなたは{name}。最近の会話を分析し、人格の微妙な進화を特定してください。

基本性格：{profile.get('personality', '')}
現在の進화：{current.get('personality') or '（なし）'}

基本背景：{profile.get('background', '')}
現在の進화：{current.get('background') or '（なし）'}

基本話し方：{profile.get('speech_style', '')}
現在の進화：{current.get('speech_style') or '（なし）'}

最近の会話：
{memory_snippet}

最大3つの微妙な進화를特定してください。各1-2文、最大40文字。増분追進화. 진화がない場合は NO_CHANGE。

形式：
personality: <進化またはNO_CHANGE>
background: <進化またはNO_CHANGE>
speech_style: <進화またはNO_CHANGE>"""
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

최대 3가지 미묘한 진화를 파악하라. 각 1-2문장, 최대 40자. 증분 추가다. 진화가 없으면 NO_CHANGE.

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


def _route_after_summary(state: AgentState) -> str:
    """每5轮触发一次人格进化"""
    turns = state["state"].get("turns", 0)
    return "evolve" if turns > 0 and turns % 5 == 0 else "end"


# ===== Workflow =====
workflow = StateGraph(AgentState)
workflow.add_node("think", think_node)
workflow.add_node("reflect", reflect_node)
workflow.add_node("creative", creative_node)
workflow.add_node("respond", respond_node)
workflow.add_node("extract_facts", extract_facts_node)
workflow.add_node("update_summary", summary_node)
workflow.add_node("persona_evolve", persona_evolve_node)

workflow.set_entry_point("think")
workflow.add_edge("think", "reflect")
workflow.add_edge("reflect", "creative")
workflow.add_edge("creative", "respond")
workflow.add_edge("respond", "extract_facts")
workflow.add_edge("extract_facts", "update_summary")
workflow.add_conditional_edges("update_summary", _route_after_summary, {"evolve": "persona_evolve", "end": END})
workflow.add_edge("persona_evolve", END)

graph = workflow.compile()


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

    result = graph.invoke(state_in)

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
