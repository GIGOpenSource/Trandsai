import asyncio
import json
import re

from fastapi import Depends
from fastapi.responses import StreamingResponse

from api.admin_common import (
    _get_error_msg,
    admin_auth_required,
    logger,
    router,
)
from core.state import get_companion_manager
from services.agent_utils import llm_content_to_str
from services.culture_data import (
    BATCH_GENERATION_ALL_LANGS_ORDER,
    build_batch_profiles,
    get_batch_persona_output_instruction,
    get_cultural_context,
    normalize_batch_generation_lang,
)
from services.async_tasks import start_avatar_generation

# ===== Companions: Batch Generate =====

# ===== 批量生成人设的 fallback 文案（按语言） =====
_BATCH_FALLBACK_TEMPLATES = {
    "zh": {
        "background": "{name}来自{city}，是一位{age}岁的{gender}性，性格{personality}，有着丰富而独特的人生经历与内心世界。",
        "speech_style": "说话风格自然真实，充满{personality}的特质，语气亲切而富有个性。",
    },
    "en": {
        "background": "{name} is from {city}, a {age}-year-old {gender} with a {personality} personality, possessing a rich and unique life experience and inner world.",
        "speech_style": "Speaks in a natural and authentic style, full of {personality} traits, with a warm and distinctive tone.",
    },
    "ja": {
        "background": "{name}は{city}出身で、{age}歳の{gender}性です。性格は{personality}で、豊かで独特な人生経験と内面世界を持っています。",
        "speech_style": "自然でリアルな話し方で、{personality}の特質に満ち、親しみやすく個性的な口調です。",
    },
    "ko": {
        "background": "{name}은(는) {city} 출신의 {age}세 {gender}로, {personality}한 성격을 가지며 풍부하고 독특한 인생 경험과 깊이 있는 내면을 가지고 있습니다.",
        "speech_style": "자연스럽고 진실된 말투로, {personality}한 특성이 가득하며 친근하고 개성 넘치는 어조입니다.",
    },
    "pt": {
        "background": "{name} é de {city}, uma pessoa de {age} anos, do sexo {gender}, com personalidade {personality}, possuindo uma rica e única experiência de vida e mundo interior.",
        "speech_style": "Estilo de fala natural e autêntico, cheio de traços {personality}, com um tom caloroso e distinto.",
    },
    "es": {
        "background": "{name} es de {city}, una persona de {age} años, de sexo {gender}, con personalidad {personality}, poseyendo una rica y única experiencia de vida y mundo interior.",
        "speech_style": "Estilo de habla natural y auténtico, lleno de rasgos {personality}, con un tono cálido y distintivo.",
    },
    "id": {
        "background": "{name} berasal dari {city}, seorang {gender} berusia {age} tahun, berkepribadian {personality}, memiliki pengalaman hidup dan dunia batin yang kaya serta unik.",
        "speech_style": "Gaya bicara yang natural dan autentik, penuh dengan karakteristik {personality}, dengan nada yang hangat dan penuh kepribadian.",
    },
}

# 性别映射（用于英文等非中文语言）
_GENDER_MAP = {
    "zh": {"男": "男", "女": "女"},
    "en": {"男": "male", "女": "female"},
    "ja": {"男": "男", "女": "女"},
    "ko": {"男": "남", "女": "여"},
    "pt": {"男": "masculino", "女": "feminino"},
    "es": {"男": "masculino", "女": "feminino"},
    "id": {"男": "laki-laki", "女": "perempuan"},
}


_BATCH_PERSONA_PROMPT = """你是一个专业的人物设定作家。请根据以下基础信息列表，为每个角色生成完整、立体、真实的人设。

要求（必须严格遵守，确保地区、语言与上表基础信息高度一致）：
1. 每个角色的内容必须和其基础信息（姓名、年龄、性别、城市、性格、MBTI）高度一致，不得写成与**城市/姓名文化圈**不符的海外架空背景，除非上表能支撑
2. 内容要口语化、有画面感、真实可信、生动具体，不要模板化或泛泛而谈
3. 成长经历（life_story）必须包含：童年、青少年、成年、原生家庭影响、重大转折点、具体事件
4. **所有字段必须完整填充**，不能为空、占位符或过短，必须达到括号指定的字数，使用丰富细节、个人故事、具体例子使人物立体有血有肉
5. 输出语言与内容一致性（极其重要，违反则视为错误输出）：
{output_instruction}
6. JSON 必须有效，每个字段都是独立、连贯的段落文本

{locale_banner}

{cultural_context}

基础信息列表：
{profiles_text}

请直接返回一个 JSON 数组，每个元素对应一个角色的人设，不要返回任何解释文字。格式如下：
[
  {{
    "name": "角色姓名（必须与输入一致）",
    "background": "背景故事（150-300字）",
    "speech_style": "说话风格（50-150字）",
    "hobbies": "兴趣爱好（50-100字）",
    "values": "核心价值观（50-100字）",
    "fears": "内心脆弱点（50-100字）",
    "love_view": "恋爱观（50-100字）",
    "daily_routine": "典型一天（80-150字）",
    "favorite_things": "喜欢的东西（50-100字）",
    "life_story": "成长经历（200-400字）",
    "cultural_values": "文化价值观（100-200字）",
    "gender_perspective": "性别视角（80-150字）"
  }}
]
"""


def _extract_json_array(text: str) -> list:
    """从 LLM 返回中提取 JSON 数组"""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return []


async def _batch_generate_companions_core(data: dict):
    """批量生成智能体的核心逻辑（异步生成器，用于 SSE 流式返回进度）"""
    from services.agent import get_llm
    from langchain_core.messages import SystemMessage

    count = data.get("count", 5)
    lang = normalize_batch_generation_lang(data.get("lang", "zh"))
    gender = data.get("gender")
    sexual_orientation = data.get("sexual_orientation")

    # 限制范围，支持最多50个以满足需求
    count = max(1, min(count, 50))

    # 生成基础属性（姓名/城市/性格与 lang 同文化圈一致）
    base_profiles = build_batch_profiles(lang, count, gender=gender, sexual_orientation=sexual_orientation)

    cultural_context = get_cultural_context(lang)
    output_instruction = get_batch_persona_output_instruction(lang)
    locale_banner = (
        f"【本批目标语言码 / target_language_code】{lang}\n"
        f"（上表「姓名、城市、性格」已从该语言/地区词库抽选，长文须与此一致。）\n"
    )

    # 按批次调用 LLM（每批最多5个）
    batch_size = 5
    all_generated = []
    total_batches = (len(base_profiles) + batch_size - 1) // batch_size

    for batch_idx, start in enumerate(range(0, len(base_profiles), batch_size)):
        batch = base_profiles[start:start + batch_size]
        profiles_text = "\n\n".join(
            f"角色 {i+1}:\n"
            f"- 姓名: {p['name']}\n"
            f"- 年龄: {p['age']}\n"
            f"- 性别: {p['gender']}\n"
            f"- 城市: {p['city']}\n"
            f"- 性格: {p['personality']}\n"
            f"- MBTI: {p['mbti']}\n"
            f"- 性取向: {p['sexual_orientation']}"
            for i, p in enumerate(batch)
        )

        prompt = _BATCH_PERSONA_PROMPT.format(
            cultural_context=cultural_context,
            profiles_text=profiles_text,
            output_instruction=output_instruction,
            locale_banner=locale_banner,
        )

        try:
            # 使用线程池执行同步LLM调用，避免阻塞异步事件循环（关键优化）
            loop = asyncio.get_event_loop()
            def _sync_llm_invoke():
                llm = get_llm(temperature=0.9, max_tokens=1024)
                resp = llm.invoke([SystemMessage(content=prompt)])
                return resp

            resp = await loop.run_in_executor(None, _sync_llm_invoke)
            text = llm_content_to_str(getattr(resp, "content", ""))
            generated_list = _extract_json_array(text)

            # 合并基础属性和生成的详细人设
            for i, gen in enumerate(generated_list):
                if i < len(batch):
                    base = batch[i]
                    fallback = _BATCH_FALLBACK_TEMPLATES.get(lang, _BATCH_FALLBACK_TEMPLATES["zh"])
                    gender_local = _GENDER_MAP.get(lang, _GENDER_MAP["zh"]).get(base["gender"], base["gender"])

                    background = gen.get("background") or ""
                    if len(background) < 5:
                        background = fallback["background"].format(
                            name=base["name"],
                            city=base["city"],
                            age=base["age"],
                            gender=gender_local,
                            personality=base["personality"],
                        )
                    speech_style = gen.get("speech_style") or ""
                    if len(speech_style) < 5:
                        speech_style = fallback["speech_style"].format(
                            name=base["name"],
                            city=base["city"],
                            age=base["age"],
                            gender=gender_local,
                            personality=base["personality"],
                        )

                    profile_data = {
                        "name": base["name"],
                        "gender": base["gender"],
                        "age": base["age"],
                        "city": base["city"],
                        "personality": base["personality"],
                        "mbti": base["mbti"],
                        "sexual_orientation": base["sexual_orientation"],
                        "background": background,
                        "speech_style": speech_style,
                        "hobbies": gen.get("hobbies") or "",
                        "values": gen.get("values") or "",
                        "fears": gen.get("fears") or "",
                        "love_view": gen.get("love_view") or "",
                        "daily_routine": gen.get("daily_routine") or "",
                        "favorite_things": gen.get("favorite_things") or "",
                        "life_story": gen.get("life_story") or "",
                        "cultural_values": gen.get("cultural_values") or "",
                        "gender_perspective": gen.get("gender_perspective") or "",
                        "language": lang,
                    }
                    # 保存到数据库
                    cm = get_companion_manager()
                    companion = cm.create(profile_data)
                    # 启动后台异步生成头像
                    if companion.profile.avatar_url == "__GENERATING__":
                        start_avatar_generation(companion.profile.id, companion.profile.model_dump())
                    all_generated.append(companion.to_dict())
                    # 每成功创建 1 个智能体即推送进度，进度条与数量同步
                    yield json.dumps({
                        "type": "progress",
                        "batch": batch_idx + 1,
                        "total_batches": total_batches,
                        "current": len(all_generated),
                        "total": count,
                    }, ensure_ascii=False)

        except Exception as e:
            logger.warning("Batch generate failed in batch %s: %s", batch_idx + 1, e)
            yield json.dumps({
                "type": "error",
                "batch": batch_idx + 1,
                "message": str(e),
                "current": len(all_generated),
                "total": count,
            }, ensure_ascii=False)
            continue

    yield json.dumps({
        "type": "complete",
        "created": len(all_generated),
        "companions": all_generated,
    }, ensure_ascii=False)


@router.post("/api/admin/companions/batch-generate", summary="批量生成伴侣")
async def admin_batch_generate_companions(data: dict, _token: str = Depends(admin_auth_required)):
    """批量生成智能体（传统接口，保持兼容）"""
    results = []
    async for event in _batch_generate_companions_core(data):
        event_data = json.loads(event)
        if event_data["type"] == "complete":
            return {
                "ok": True,
                "created": event_data["created"],
                "companions": event_data["companions"],
            }
        elif event_data["type"] == "error":
            results.append(event_data)
    return {"ok": True, "created": 0, "companions": []}


@router.post("/api/admin/companions/batch-generate-stream", summary="流式批量生成伴侣")
async def admin_batch_generate_companions_stream(data: dict, _token: str = Depends(admin_auth_required)):
    """批量生成智能体（SSE 流式接口，实时返回进度）"""

    async def event_generator():
        async for event in _batch_generate_companions_core(data):
            yield f"data: {event}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _batch_generate_all_langs_core(data: dict):
    """为所有语言批量生成智能体的核心逻辑（异步生成器）"""
    count_per_lang = max(1, min(data.get("count_per_lang", 10), 50))
    gender = data.get("gender")
    sexual_orientation = data.get("sexual_orientation")
    valid_langs = list(BATCH_GENERATION_ALL_LANGS_ORDER)
    total_all = count_per_lang * len(valid_langs)
    created_all = 0

    for idx, lang in enumerate(valid_langs):
        yield json.dumps({
            "type": "lang_start",
            "lang": lang,
            "lang_index": idx + 1,
            "total_langs": len(valid_langs),
            "count_per_lang": count_per_lang,
            "created_all": created_all,
            "total_all": total_all,
        }, ensure_ascii=False)

        lang_data = {
            "count": count_per_lang,
            "lang": lang,
            "gender": gender,
            "sexual_orientation": sexual_orientation,
        }

        async for event in _batch_generate_companions_core(lang_data):
            event_data = json.loads(event)
            if event_data["type"] == "progress":
                created_all = event_data["current"] + idx * count_per_lang
                yield json.dumps({
                    "type": "progress",
                    "lang": lang,
                    "lang_index": idx + 1,
                    "total_langs": len(valid_langs),
                    "current_lang": event_data["current"],
                    "total_lang": event_data["total"],
                    "created_all": created_all,
                    "total_all": total_all,
                }, ensure_ascii=False)
            elif event_data["type"] == "error":
                yield json.dumps({
                    "type": "error",
                    "lang": lang,
                    "batch": event_data.get("batch"),
                    "message": event_data.get("message"),
                    "created_all": created_all,
                    "total_all": total_all,
                }, ensure_ascii=False)
            elif event_data["type"] == "complete":
                created_all = event_data["created"] + idx * count_per_lang

    yield json.dumps({
        "type": "complete",
        "created_all": created_all,
        "total_all": total_all,
    }, ensure_ascii=False)


@router.post("/api/admin/companions/batch-generate-all-langs-stream", summary="多语言流式批量生成伴侣")
async def admin_batch_generate_all_langs_stream(data: dict, _token: str = Depends(admin_auth_required)):
    """为所有语言批量生成智能体（SSE 流式接口）"""

    async def event_generator():
        async for event in _batch_generate_all_langs_core(data):
            yield f"data: {event}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
