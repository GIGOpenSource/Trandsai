import asyncio
import functools
import json
import logging
import random
import time
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from langchain_core.messages import SystemMessage
from pydantic import BaseModel
from starlette.websockets import WebSocketState
from typing import List, Optional, Tuple

from core.auth import verify_token as redis_verify_token
from core.database import UserORM, get_db
from core.config import (
    _AGENT_TIMEOUT_MESSAGE,
    _DUPLICATE_USER_MESSAGE,
    _MULTI_MESSAGE_AGENT_PREFIX,
    _QUEUE_COALESCED_MESSAGE,
    detect_leave_intent,
    split_response,
)
from core.state import get_companion_manager, get_session, set_session
from services.companion_manager import hydrate_user_affection_turns
from services.agent import build_system_prompt, get_llm, run_agent, get_content_restriction
from services.agent_utils import build_dialogue_time_context
from services.culture_data import get_cultural_context
from services.memory import normalize_message_text_for_dedup
from services.image_generation import generate_avatar_prompt, generate_image_with_cache
from core.i18n import (
    _LLM_WAIT_FILLER,
    normalize_ui_language,
    _WS_ACCESS_DENIED,
    _WS_AUTH_FAILED,
    _WS_CHAT_UNEXPECTED_ERROR,
    _WS_COMPANION_NOT_FOUND,
)
from services.async_tasks import start_avatar_generation

from fastapi import Depends, Header
from core.permissions import IsAuthenticated, IsOwner, IsAdmin
from core.dependencies import require_permissions, get_current_user
router = APIRouter(
    tags=["伴侣"]
)
logger = logging.getLogger(__name__)

# 单次从 WS 队列合并处理的用户消息条数上限：避免一次合并过多导致模型/记忆语义「串」、且 empty() 在竞态下不可靠
_WS_BURST_MAX_MESSAGES = 10

# 带括号「思考」时，仅约本概率的轮次向客户端发送 💭 toast（约每 10 次用户-AI 交互 1～2 次展示）
_THINK_TOAST_UI_PROBABILITY = 0.15

# 极短用户文不参与「重复发送」去重，避免「在吗」连发被拦
_DUPLICATE_CHECK_MIN_LEN = 4

# 半角 () 与全角（）：整段匹配，用于在全文上按顺序切出「思考块」与「正文块」
_BRACKET_BLOCK_PATTERN = re.compile(r"\([^)]*\)|（[^）]*）")
# 去掉括号块（供正文条内二次清理）
_BRACKET_STRIP_PATTERN = re.compile(r"\([^)]*\)|（[^）]*）")
_THINK_INNER_MAX_RAW = 6000
_THINK_TOAST_WS_MAX = 2400

# 混合正文里「句末标点 + 空白 + 下一句（中文或左引号/左括号起头）」视作换条
_SOFT_BREAK_AFTER_CJK_SENTENCE = re.compile(
    r"(?<=[。？！])(?:[ \t\u00a0\u3000]+)(?=[\u4e00-\u9fff（「『《])"
)
# 句末标点后无空白、直接接下一句中文时，也换条（避免「…是吧？先进来」整段一条气泡）
_TIGHT_BREAK_AFTER_CJK_SENTENCE = re.compile(
    r"(?<=[。？！])(?=[\u4e00-\u9fff（「『《])"
)


def _burst_mood_from_user_text(s: str) -> str:
    """分条节奏：激动连发 / 平静长段 / 普通（影响 max_chars 与条间暂停）。"""
    t = (s or "").strip()
    if not t:
        return "neutral"
    ex = t.count("!") + t.count("！")
    if ex >= 2 or "哈哈" in t or "哈哈哈" in t or "ww" in t.lower():
        return "excited"
    if t.count("？") + t.count("?") >= 4:
        return "excited"
    if len(t) > 120 and t.count("。") >= 3 and ex == 0:
        return "calm"
    return "neutral"


def _inject_soft_linebreaks_for_mixed_messages(s: str) -> str:
    """在纯正文段内补换行以便拆多条气泡；均从左到右扫描，不改变未匹配片段顺序。
    先「句末+直接接中文」再「句末+空白+中文」，避免与 split_response 的换行规则打架。"""
    if not (s or "").strip():
        return s or ""
    s = _TIGHT_BREAK_AFTER_CJK_SENTENCE.sub("\n", s)
    s = _SOFT_BREAK_AFTER_CJK_SENTENCE.sub("\n", s)
    return s


def _split_thinks_and_text(s: str) -> list[tuple[str, str]]:
    """从左到右扫描整段回复，按出现顺序产出 ('think', …) / ('text', …)；与叙事时间线一致，勿重排。"""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _BRACKET_BLOCK_PATTERN.finditer(s):
        if m.start() > pos:
            chunk = s[pos : m.start()]
            if chunk.strip():
                out.append(("text", chunk))
        raw = m.group(0)
        if raw.startswith("(") and raw.endswith(")"):
            inner = raw[1:-1].strip()
        elif raw.startswith("（") and raw.endswith("）"):
            inner = raw[1:-1].strip()
        else:
            inner = raw.strip()
        if inner:
            out.append(("think", inner[:_THINK_INNER_MAX_RAW]))
        pos = m.end()
    if pos < len(s):
        tail = s[pos:]
        if tail.strip():
            out.append(("text", tail))
    return out


async def _send_plain_assistant_bubble(
    websocket: WebSocket, companion, display_text: str
) -> str:
    """仅发送正文气泡（已不含括号）。与上条去重后相同则一般不发；极短句或约 12% 长句仍发出（拟人「嘴笨重复」）。"""
    display_text = (display_text or "").strip()
    if not display_text:
        return ""
    skip = False
    if companion is not None:
        last_a = companion.memory.short_term.get_last_assistant_content()
        if last_a is not None:
            n_new = normalize_message_text_for_dedup(display_text)
            n_last = normalize_message_text_for_dedup(last_a)
            if n_new == n_last:
                if len(n_new) < 10:
                    skip = False
                else:
                    skip = random.random() < 0.88
    if not skip:
        await websocket.send_text(
            json.dumps({"type": "message", "role": "assistant", "text": display_text})
        )
    return "" if skip else display_text


async def _keepalive_during_agent(websocket: WebSocket, agent_task, language: str) -> None:
    """长推理时续传 typing、并偶发极短弱反馈，避免长静默只像未响应。"""
    try:
        await asyncio.sleep(0.9)
        if agent_task.done():
            return
        lk = normalize_ui_language(language)
        lines = _LLM_WAIT_FILLER.get(lk) or _LLM_WAIT_FILLER.get("zh")
        if lines:
            try:
                await websocket.send_text(
                    json.dumps({"type": "filler", "text": random.choice(lines)})
                )
            except Exception:
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        return
    n = 0
    while not agent_task.done():
        n += 1
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps({"type": "typing"}))
        except Exception:
            return
        try:
            wait = 1.85 if n < 4 else 2.35
            await asyncio.wait_for(asyncio.shield(agent_task), timeout=wait)
            return
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            if agent_task.done():
                return
        except Exception:
            return


async def _deliver_assistant_content(
    websocket: WebSocket,
    companion,
    response_text: str,
    *,
    message_queue: Optional[asyncio.Queue] = None,
    show_think_toasts: bool = False,
    delivery_mood: str = "neutral",
    user_short_term=None,
) -> Tuple[List[str], bool]:
    """严格按 `_split_thinks_and_text` 的顺序下发：每条 think 可发 toast（由 show_think_toasts 控制频次），再处理紧随的正文块；
    正文块内先软换行再 split_response；分条节奏由 delivery_mood（激动/平静/普通）影响 max_chars 与条间是否打 typing、暂停长短。
    不再推送 LangGraph think 节点原文。返回 (已持久化的正文条列表, 是否被队列中断)。"""
    sent_segments: list[str] = []
    parts = _split_thinks_and_text(response_text or "")
    if not parts:
        return sent_segments, False

    bubble_idx = 0
    think_toast_sent = False  # 本轮若展示思考，仅发第一条 💭，避免同条回复内多条刷屏

    async def _interrupted() -> bool:
        return bool(message_queue is not None and not message_queue.empty())

    for kind, payload in parts:
        if await _interrupted():
            return sent_segments, True
        if kind == "think":
            if not show_think_toasts or think_toast_sent:
                continue
            inner = (payload or "").strip()
            if not inner:
                continue
            if len(inner) > _THINK_TOAST_WS_MAX:
                inner = inner[: _THINK_TOAST_WS_MAX - 1] + "…"
            try:
                await websocket.send_text(
                    json.dumps({"type": "toast", "text": f"💭 {inner}"})
                )
            except Exception:
                return sent_segments, True
            think_toast_sent = True
            await asyncio.sleep(0.12)
            continue

        # 正文块：去残留括号 → 句间软换行 → 再按 split_response 分条（混合时拆多条消息）
        body = _BRACKET_STRIP_PATTERN.sub("", payload).strip()
        if not body:
            continue
        body = _inject_soft_linebreaks_for_mixed_messages(body)
        if delivery_mood == "excited":
            _mc = random.randint(100, 140)
        elif delivery_mood == "calm":
            _mc = random.randint(175, 235)
        else:
            _mc = random.randint(128, 175)
        subsegs = split_response(body, max_chars=_mc)
        for j, sub in enumerate(subsegs):
            if await _interrupted():
                return sent_segments, True
            sub_clean = _BRACKET_STRIP_PATTERN.sub("", (sub or "").strip()).strip()
            if not sub_clean:
                continue
            if bubble_idx > 0:
                skip_typing = False
                if delivery_mood == "excited":
                    skip_typing = random.random() < 0.48
                elif delivery_mood == "calm":
                    skip_typing = random.random() < 0.22
                else:
                    skip_typing = random.random() < 0.32
                if not skip_typing:
                    try:
                        await websocket.send_text(json.dumps({"type": "typing"}))
                    except Exception:
                        return sent_segments, True
                if await _interrupted():
                    return sent_segments, True
                if delivery_mood == "excited":
                    seg_delay = random.uniform(0.1, 0.3)
                elif delivery_mood == "calm":
                    seg_delay = random.uniform(0.2, 0.5)
                else:
                    seg_delay = random.uniform(0.1, 0.4)
                await asyncio.sleep(seg_delay)
                if await _interrupted():
                    return sent_segments, True
            stored = await _send_plain_assistant_bubble(websocket, companion, sub_clean)
            if stored:
                if user_short_term:
                    # 检查是否重复
                    last = user_short_term.get_last_assistant_content()
                    from services.memory import normalize_message_text_for_dedup
                    if last is None or normalize_message_text_for_dedup(stored) != normalize_message_text_for_dedup(last):
                        user_short_term.add("assistant", stored)
                else:
                    companion.memory.add_assistant_message(stored)
                sent_segments.append(stored)
            bubble_idx += 1

    return sent_segments, False


async def require_login_user(
        x_token: Optional[str] = Header(None, alias="x-token"),
) -> int:
    """REST 接口：必须携带有效用户 Token（与 WebSocket IM 一致）。"""
    uid = redis_verify_token(x_token) if x_token else None
    if uid is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return uid


def _assert_companion_user_access(companion, user_id: int) -> None:
    """验证用户是否有权访问该 companion。所有登录用户都可以访问所有智能体。"""
    # 所有登录用户都可以访问所有智能体
    pass


# ===== 主动消息相关 =====

def _companion_city_str(companion) -> Optional[str]:
    try:
        c = (companion.profile.city or "").strip()
        return c or None
    except Exception:
        return None


def _build_proactive_prompt(companion, lang: str, time_context: str, user_short_term=None) -> str:
    """构建 AI 主动发消息的 prompt"""
    lang = normalize_ui_language(lang)
    system_text = build_system_prompt(companion.profile.model_dump(), lang, turns=companion.state.turns)
    name = companion.profile.name
    mood = companion.state.mood
    affection = companion.state.affection
    # 优先使用用户隔离的短期记忆
    if user_short_term:
        recent = user_short_term.get_recent(6)
    else:
        recent = companion.memory.short_term.get_recent(6)
    recent_text = "\n".join(
        f"{'他' if m['role'] == 'user' else name}：{m['content']}"
        for m in recent
    )

    # 根据亲密度获取内容安全限制文本
    restriction_text = get_content_restriction(lang, affection)

    if lang == "en":
        return f"""{system_text}

【Current State】
Mood: {mood} | Affection: {affection}

【User local time & season】
{time_context}

【Recent Chat】
{recent_text}

They haven't replied for a while. As {name}, send a proactive message — check on them, be playful, or share a life fragment.
Requirements:
- Completely colloquial, like texting a lover
- 2-3 short sentences, each under 25 words
- Must end with a hook (question or invitation to reply)
- Output the reply directly, no prefix

{restriction_text}"""

    if lang == "ja":
        return f"""{system_text}

【現在の状態】
感情：{mood} | 親密度：{affection}

【相手の現地・季節・時刻】
{time_context}

【最近のやり取り】
{recent_text}

相手がしばらく返信してこない。{name}として、率先してメッセージを送って——気遣ったり、甘えたり、生活の一片をシェアしたり。
要求：
- 完全に口語体、伙伴とのLINEのような感じ
- 2〜3文、それぞれ25字以内
- 最後は必ずフック（質問や返信の誘い）
- 直接返信内容を出力、前置き不要

{restriction_text}"""

    if lang == "ko":
        return f"""{system_text}

【현재 상태】
기분：{mood} | 친밀도：{affection}

【상대 현지·계절·시각】
{time_context}

【최근 대화】
{recent_text}

상대가 잠깐 동안 답장이 없어. {name}으로서, 능동적으로 메시지를 본내——챙겨주거나 애교 부리거나 일상의 조각을 공유하거나.
요구사항：
- 완전히 구어체, 연인이랑 카톡하는 느낌
- 2~3문장, 각각 25자 이내
- 마지막은 반드시 훅（질문이나 답장 유도）
- 바로 답장 내용을 출력, 전치사 불필요

{restriction_text}"""

    if lang == "pt":
        return f"""{system_text}

【Estado Atual】
Humor: {mood} | Proximidade: {affection}

【Hora local e estação do usuário】
{time_context}

【Conversas Recentes】
{recent_text}

A pessoa não responde há um tempo. Como {name}, envie uma mensagem proativa — se preocupe, seja carinhosa, ou compartilhe um pedacinho da sua vida.
Requisitos:
- Completamente coloquial, como conversar com um namorado/namorada no WhatsApp
- 2-3 frases curtas, cada uma com menos de 25 palavras
- Deve terminar com um gancho (pergunta ou convite para responder)
- Saída direta do conteúdo da resposta, sem prefixo

{restriction_text}"""

    if lang == "es":
        return f"""{system_text}

【Estado Actual】
Ánimo: {mood} | Cercanía: {affection}

【Hora local y estación del usuario】
{time_context}

【Conversaciones Recientes】
{recent_text}

La persona no responde desde hace un rato. Como {name}, envía un mensaje proactivo — preocúpate, sé cariñoso/a, o comparte un pedacito de tu vida.
Requisitos:
- Completamente coloquial, como chatear con tu pareja
- 2-3 frases cortas, cada una con menos de 25 palabras
- Debe terminar con un gancho (pregunta o invitación a responder)
- Salida directa del contenido de la respuesta, sin prefijo

{restriction_text}"""

    if lang == "id":
        return f"""{system_text}

【Status Saat Ini】
Suasana hati: {mood} | Kedekatan: {affection}

【Waktu lokal & musim pengguna】
{time_context}

【Obrolan Terbaru】
{recent_text}

Orang itu belum membalas sebentar. Sebagai {name}, kirim pesan proaktif — perhatikan, manja, atau bagikan cuplikan kehidupanmu.
Persyaratan:
- Sepenuhnya santai, seperti chat dengan pacar di WhatsApp
- 2-3 kalimat pendek, masing-masing kurang dari 25 kata
- Harus diakhiri dengan kaitan (pertanyaan atau ajakan untuk membalas)
- Keluarkan langsung isi balasan, tanpa awalan

{restriction_text}"""

    # 默认中文
    return f"""{system_text}

【当前状态】
情绪：{mood} | 亲密度：{affection}

【用户当地时间与季节】
{time_context}

【最近对话】
{recent_text}

用户已经有一会儿没回消息了。请你作为{name}，主动发条消息关心一下、撒个娇、或者分享一个生活碎片。
要求：
- 完全口语化，像真实伙伴发微信
- 2-3句话，每句不超过25字
- 不要以陈述句结尾，必须带一个钩子（问题或邀请回复）
- 直接输出回复内容，不要加任何前缀

{restriction_text}"""


async def _send_proactive_message(websocket: WebSocket, companion, lang: str, companion_id: str, user_short_term=None):
    """定时发送主动消息"""
    try:
        await asyncio.sleep(random.uniform(90, 180))

        # 检查连接是否仍然打开
        if websocket.client_state == WebSocketState.DISCONNECTED:
            return

        try:
            await websocket.send_text(json.dumps({"type": "typing"}))
        except Exception:
            return

        sess = get_session(companion_id) or {}
        time_ctx = build_dialogue_time_context(
            language=lang,
            tz_iana=(sess.get("client_tz") or "").strip() or None,
            tz_offset_minutes=sess.get("client_tz_offset"),
            companion_city=_companion_city_str(companion),
        )
        prompt = _build_proactive_prompt(companion, lang, time_ctx, user_short_term=user_short_term)
        llm = get_llm()
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: llm.invoke([SystemMessage(content=prompt)]))
        text = resp.content.strip()

        await asyncio.sleep(random.uniform(0.5, 1.0))

        try:
            aff = float(getattr(companion.state, "affection", 0) or 0)
            show_think = random.random() < _THINK_TOAST_UI_PROBABILITY * (
                0.35 + 0.65 * min(1.0, aff / 100.0)
            )
            await _deliver_assistant_content(
                websocket,
                companion,
                text,
                message_queue=None,
                show_think_toasts=show_think,
                delivery_mood="neutral",
                user_short_term=user_short_term,
            )
        except Exception:
            return

        sess_done = get_session(companion_id) or {}
        uid = sess_done.get("user_id")
        companion.save_state(uid if isinstance(uid, int) else None)
    except Exception as e:
        logger.warning(
            "Proactive message generation failed for companion %s: %s",
            getattr(companion, "profile", None) and companion.profile.id,
            e,
        )


_PERSONA_GENERATE_PROMPT = """你是一个专业的人物设定作家。请根据以下基础信息，生成一个完整、立体、真实的虚拟伙伴/伴侣人设。

要求：
1. 生成的内容必须和已知的基础信息（姓名、年龄、性别、城市、性格、MBTI）高度一致
2. 内容要口语化、有画面感、真实可信，不要模板化
3. 成长经历要包含：童年、青少年、成年、原生家庭影响、重大转折点、内心创伤与成长
4. 每个字段都要独立且丰富

基础信息：
- 姓名：{name}
- 年龄：{age}
- 性别：{gender}
- 性取向：{sexual_orientation}
- 城市：{city}
- 性格：{personality}
- MBTI：{mbti}

{cultural_context}

请直接返回一个 JSON 对象，不要返回任何解释文字。JSON 格式如下：
{{
  "background": "...",
  "speech_style": "...",
  "hobbies": "...",
  "values": "...",
  "fears": "...",
  "love_view": "...",
  "daily_routine": "...",
  "favorite_things": "...",
  "life_story": "...",
  "cultural_values": "...",
  "gender_perspective": "..."
}}
"""


def _extract_json(text: str) -> dict:
    """从 LLM 返回中提取 JSON"""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试正则提取 JSON 块
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return json.loads(match.group(0))
        raise


class PersonaGenerateRequest(BaseModel):
    name: str
    age: int = 22
    gender: str = "女"
    sexual_orientation: str = ""
    city: str
    personality: str
    mbti: str = ""
    lang: str = "zh"


@router.post("/companions/generate",tags=["伴侣"], summary="AI 生成伴侣人设"
             ,)
async def api_generate_persona(data: PersonaGenerateRequest):
    """根据基础信息 AI 自动生成完整人设"""
    name = data.name
    age = data.age
    gender = data.gender
    sexual_orientation = data.sexual_orientation
    city = data.city
    personality = data.personality
    mbti = data.mbti
    lang = normalize_ui_language(data.lang)

    if not name or not city or not personality:
        raise HTTPException(status_code=400, detail="姓名、城市和性格为必填项")

    orientation_label = {
        "heterosexual": "异性恋",
        "homosexual": "同性恋",
        "bisexual": "双性恋",
        "pansexual": "泛性恋",
        "asexual": "无性恋",
        "secret": "保密",
    }.get(sexual_orientation, "")

    cultural_context = get_cultural_context(lang)

    prompt = _PERSONA_GENERATE_PROMPT.format(
        name=name, age=age, gender=gender,
        sexual_orientation=orientation_label or "未指定",
        city=city,
        personality=personality, mbti=mbti or "未知",
        cultural_context=cultural_context,
    )

    try:
        llm = get_llm(temperature=0.9, max_tokens=1024)
        resp = llm.invoke([SystemMessage(content=prompt)])
        text = resp.content.strip() if hasattr(resp, "content") else str(resp)
        result = _extract_json(text)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败: {str(e)}")


@router.post("/companions",
             summary="创建伴侣",
             description="创建新的AI伴侣，需要登录",
             response_model=dict,
             responses={
                 200: {
                     "description": "创建成功",
                     "content": {
                         "application/json": {
                             "example": {
                                 "id": "comp_001",
                                 "name": "小美",
                                 "age": 23,
                                 "gender": "female",
                                 "city": "上海",
                                 "avatar_url": "https://example.com/avatar.jpg"
                             }
                         }
                     }
                 },
                 400: {"description": "参数错误"},
                 401: {"description": "未登录"}
             })
async def api_create_companion(
        data: dict,
        user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """用户端创建伴侣 - 需要登录"""
    from core.database import UserORM

    # 获取用户信息，设置 created_by
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=400, detail="用户不存在")

    # 设置 created_by 为 user_id
    data["created_by"] = str(user_id)

    try:
        companion = get_companion_manager().create(data)
        # 启动后台异步生成头像
        if companion.profile.avatar_url == "__GENERATING__":
            start_avatar_generation(companion.profile.id, companion.profile.model_dump())
        return companion.to_dict(user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/companions",
            summary="获取伴侣列表",
            description="获取所有可用的AI伴侣列表，支持多种筛选条件",
            response_model=list,
            responses={
                200: {
                    "description": "成功",
                    "content": {
                        "application/json": {
                            "example": [
                                {
                                    "id": "comp_001",
                                    "name": "小美",
                                    "age": 23,
                                    "gender": "female",
                                    "city": "上海",
                                    "avatar_url": "https://example.com/avatar1.jpg",
                                    "affection": 75
                                }
                            ]
                        }
                    }
                },
                401: {"description": "未登录"}
            })
async def api_list_companions(
        x_token: Optional[str] = Header(None, alias="x-token"),
        user_id: int = Depends(require_permissions(IsAuthenticated)),
        special: Optional[str] = Query(None, description="特殊场景筛选，message=消息场景（自己创建的所有+别人创建的亲密度>0.01）"),
        intimacy_value: float = Query(0, description="亲密度阈值，返回亲密度>=该值的智能体，默认0不过滤"),
        is_created: Optional[str] = Query(None, description="创建者筛选：true=只返回自己创建的，false=只返回别人创建的，不传=全部")
):
    """获取当前用户的 companions 列表

          Args:
              special: 特殊场景筛选
                  - "message": 消息场景 —— 自己创建的所有 + 别人创建的亲密度>0.01（忽略 intimacy_value 和 is_created）
              intimacy_value: 亲密度阈值（默认0），返回亲密度 >= 该值的智能体
              is_created: 创建者筛选
                  - "true": 只返回自己创建的智能体
                  - "false": 只返回别人创建的智能体
                  - 不传: 返回所有智能体

          场景示例：
              1. 消息场景：special=message
              2. 首页：intimacy_value=0.001
              3. 机器人页面：无参数（默认）
              4. 我的机器人页面：is_created=true
              5. 我的恋人：intimacy_value=n（用户设置的亲密度值）
              6. 亲密度记录：intimacy_value=0.001
          """
    # 解析 is_created 字符串为 bool 或 None
    is_created_bool = None
    if is_created is not None:
        is_created_bool = is_created.lower() == "true"

    return get_companion_manager().list_all_for_any(
        user_id=user_id,
        special=special,
        intimacy_value=intimacy_value,
        is_created=is_created_bool,
    )


@router.get("/companions/{companion_id}", summary="获取伴侣详情")
async def api_get_companion(
        companion_id: str,
        user_id: int = Depends(require_permissions(IsAuthenticated))
):
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return companion.to_dict(user_id=user_id)

@router.get("/companions/{companion_id}/messages", summary="获取聊天记录")
async def api_get_messages(
  companion_id: str,
  limit: int = 20,
  offset: int = 0,
  user_id: int = Depends(require_permissions(IsAuthenticated))
):
  companion = get_companion_manager().get(companion_id)
  if not companion:
      raise HTTPException(status_code=404, detail="智能体不存在")
  # 只获取当前用户的聊天记录
  from services.memory import ShortTermMemory
  user_short_term = ShortTermMemory(companion_id, user_id)
  messages = user_short_term.get_recent(limit, offset)
  return {"messages": messages, "total": user_short_term.get_total_count()}


# 生成头像
@router.post("/companions/{companion_id}/generate-avatar", summary="AI 生成伴侣头像")
async def api_generate_avatar(
        companion_id: str,
        user_id: int = Depends(require_permissions(IsAuthenticated))
):
    """基于人设 AI 生成动漫风格头像"""
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail="智能体不存在")

    prompt = generate_avatar_prompt(companion.profile.model_dump())
    image_url = generate_image_with_cache(prompt, style="portrait", width=512, height=512)

    # 更新 avatar_url
    get_companion_manager().update(companion_id, {"avatar_url": image_url})
    return {"ok": True, "avatar_url": image_url}


# 删除 - 只能删除自己创建的（软删除）
@router.delete("/companions/{companion_id}", summary="删除伴侣")
async def api_delete_companion(
        companion_id: str,
        user_id: int = Depends(require_permissions(IsAuthenticated))
):
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail="智能体不存在")
    # 权限校验：只能删除自己创建的
    user_info = get_companion_manager()._get_user_info(user_id)
    if not get_companion_manager()._is_my_companion(companion, user_info):
        raise HTTPException(status_code=403, detail="只能删除自己创建的伴侣")
    # 软删除
    ok = get_companion_manager().soft_delete(companion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return {"ok": True}


@router.post("/companions/{companion_id}/clear-messages", summary="清空聊天记录")
async def api_clear_messages(
        companion_id: str,
        user_id: int = Depends(require_permissions(IsAuthenticated))
):
    """清空当前用户与该智能体的聊天记录（短期记忆），并将亲密度归零"""
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail="智能体不存在")
    # 只清空当前用户的聊天记录
    from services.memory import ShortTermMemory
    user_short_term = ShortTermMemory(companion_id, user_id)
    user_short_term.clear()
    # 重置当前用户与该智能体的亲密度
    from core.database import UserCompanionStateORM, get_db
    with get_db() as db:
        ucs = db.query(UserCompanionStateORM).filter(
            UserCompanionStateORM.user_id == user_id,
            UserCompanionStateORM.companion_id == companion_id
        ).first()
        if ucs:
            ucs.affection = 0
            ucs.turns = 0
    return {"ok": True}


@router.websocket("/ws/chat/{companion_id}")
async def ws_chat(websocket: WebSocket, companion_id: str):
    await websocket.accept()

    ui_lang_early = normalize_ui_language(websocket.query_params.get("lang", "zh"))

    # 安全优化：验证用户 token
    token = websocket.query_params.get("token")
    # token = websocket.headers.get("token")
    user_id = redis_verify_token(token) if token else None
    if not user_id:
        try:
            err = _WS_AUTH_FAILED.get(ui_lang_early, _WS_AUTH_FAILED["zh"])
            await websocket.send_text(json.dumps({"type": "error", "text": err}))
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    companion = get_companion_manager().get(companion_id)
    if not companion:
        err = _WS_COMPANION_NOT_FOUND.get(ui_lang_early, _WS_COMPANION_NOT_FOUND["zh"])
        await websocket.send_text(json.dumps({"type": "error", "text": err}))
        await websocket.close()
        return

    # 软删除拦截：已被创建者删除的伴侣，禁止对话
    if companion.profile.deleted_at:
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "text": "该伴侣已被创建者删除，无法继续对话"
            }))
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    try:
        _assert_companion_user_access(companion, user_id)
    except HTTPException as e:
        try:
            detail = (
                e.detail
                if isinstance(e.detail, str) and e.detail.strip()
                else None
            )
            err = detail or _WS_ACCESS_DENIED.get(ui_lang_early, _WS_ACCESS_DENIED["zh"])
            await websocket.send_text(json.dumps({"type": "error", "text": err}))
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    lang_param = normalize_ui_language(websocket.query_params.get("lang", "zh"))
    session_meta = get_session(companion_id)
    session_meta["lang"] = lang_param
    session_meta["user_id"] = user_id  # 记录用户ID用于后续会话
    session_meta.pop("pending_retention", None)
    session_meta.pop("last_disconnect", None)
    await set_session(companion_id, session_meta)
    user_lang = session_meta.get("lang") or "zh"

    # 创建按用户隔离的短期记忆
    from services.memory import ShortTermMemory
    user_short_term = ShortTermMemory(companion_id, user_id)

    # 消息去重：记录最近收到的消息内容和时间戳
    _recent_user_messages: list[dict] = []

    # 不在每次 WS 握手后推送「已连接」类系统提示，避免离线重连打扰用户（静默连接）

    proactive_task = None

    # 用户消息队列：解耦接收与处理，支持发送过程中被打断
    message_queue: asyncio.Queue = asyncio.Queue()

    async def _receive_loop():
        """持续接收用户消息，存入队列"""
        while True:
            try:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"text": raw}
                user_text = payload.get("text", "").strip()
                if user_text:
                    await message_queue.put(payload)
            except WebSocketDisconnect:
                await message_queue.put({"__disconnect": True})
                break
            except Exception:
                await message_queue.put({"__disconnect": True})
                break

    receiver_task = asyncio.create_task(_receive_loop())

    try:
        while True:
            # 从队列等待用户消息
            payload = await message_queue.get()
            if payload.get("__disconnect"):
                if proactive_task and not proactive_task.done():
                    proactive_task.cancel()
                prev_sess = get_session(companion_id)
                prev_lang = prev_sess.get("lang")
                next_sess = {**prev_sess, "lang": prev_lang or user_lang or "zh"}
                next_sess.pop("pending_retention", None)
                next_sess.pop("last_disconnect", None)
                await set_session(companion_id, next_sess)
                break
            first_text = payload.get("text", "").strip()
            if not first_text:
                continue

            # 与接收顺序一致：同轮紧挨着的多条用户文合并给模型；设上限条数，余量留待下一轮，避免一次吞太多语义串台
            burst_head = payload
            burst_parts: list[str] = [first_text]
            coalesce_skipped = 0
            while len(burst_parts) < _WS_BURST_MAX_MESSAGES:
                try:
                    newer = message_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if newer.get("__disconnect"):
                    await message_queue.put(newer)
                    break
                newer_text = newer.get("text", "").strip()
                if newer_text:
                    burst_parts.append(newer_text)
                    coalesce_skipped += 1

            combined_user_plain = "\n".join(burst_parts)
            lk_co = normalize_ui_language(burst_head.get("lang") or user_lang or "zh")

            if coalesce_skipped >= 2:
                co_txt = _QUEUE_COALESCED_MESSAGE.get(lk_co, _QUEUE_COALESCED_MESSAGE["zh"])
                try:
                    await websocket.send_text(json.dumps({"type": "system", "text": co_txt}))
                except Exception:
                    pass

            # 消息去重：极短句不拦（如连发「在吗」）；其余 5s 内全文相同视为连点/重复
            now = datetime.now(timezone.utc)
            _plain_stripped = combined_user_plain.strip()
            is_duplicate = False
            if len(_plain_stripped) >= _DUPLICATE_CHECK_MIN_LEN:
                is_duplicate = any(
                    m["text"] == combined_user_plain and (now - m["time"]).total_seconds() < 5
                    for m in _recent_user_messages
                )
            if is_duplicate:
                lk_dup = normalize_ui_language(burst_head.get("lang") or user_lang or "zh")
                dup_txt = _DUPLICATE_USER_MESSAGE.get(lk_dup, _DUPLICATE_USER_MESSAGE["zh"])
                try:
                    await websocket.send_text(json.dumps({"type": "system", "text": dup_txt}))
                except Exception:
                    pass
                continue
            _recent_user_messages.append({"text": combined_user_plain, "time": now})
            if len(_recent_user_messages) > 10:
                _recent_user_messages.pop(0)

            # 用户发新消息了，取消之前的主动消息定时任务
            if proactive_task and not proactive_task.done():
                proactive_task.cancel()

            # 如果有未发送完的AI回复，先保存已发送的部分（如果有）
            # 这里简化处理：已发送的内容已经展示给用户，新的回复会覆盖话题

            for line in burst_parts:
                user_short_term.add("user", line)

            has_leave_intent = detect_leave_intent(combined_user_plain)

            if len(burst_parts) > 1:
                prefix = _MULTI_MESSAGE_AGENT_PREFIX.get(lk_co, _MULTI_MESSAGE_AGENT_PREFIX["zh"])
                numbered = "\n".join(f"({i + 1}) {t}" for i, t in enumerate(burst_parts))
                user_text_for_agent = prefix + numbered
            else:
                user_text_for_agent = burst_parts[0]
            try:
                await websocket.send_text(json.dumps({"type": "typing"}))
            except WebSocketDisconnect:
                raise
            except Exception:
                continue

            t_memory = time.time()
            memory_text = companion.memory.build_prompt_context(query=combined_user_plain)
            logger.info("[TIMING] build_prompt_context: %.2fs", time.time() - t_memory)
            language = normalize_ui_language(
                burst_head.get("lang") or get_session(companion_id).get("lang") or user_lang or "zh"
            )
            session_meta = get_session(companion_id)
            session_meta["lang"] = language
            tz_pay = (burst_head.get("tz") or burst_head.get("timeZone") or "").strip()
            if tz_pay:
                session_meta["client_tz"] = tz_pay[:120]
            if burst_head.get("tz_offset") is not None:
                try:
                    session_meta["client_tz_offset"] = int(burst_head["tz_offset"])
                except (TypeError, ValueError):
                    pass
            await set_session(companion_id, session_meta)

            tz_iana_effective = (
                (burst_head.get("tz") or burst_head.get("timeZone") or "").strip()
                or (session_meta.get("client_tz") or "").strip()
                or None
            )
            if tz_iana_effective:
                tz_iana_effective = tz_iana_effective[:120]
            tz_off_effective = session_meta.get("client_tz_offset")

            hydrate_user_affection_turns(companion, user_id)

            loop = asyncio.get_event_loop()
            current_time = build_dialogue_time_context(
                language=language,
                tz_iana=tz_iana_effective,
                tz_offset_minutes=tz_off_effective
                if isinstance(tz_off_effective, int)
                else None,
                companion_city=_companion_city_str(companion),
            )
            user_gender = burst_head.get("user_gender", "")
            delivery_mood = _burst_mood_from_user_text(combined_user_plain)

            async def _run_agent_coro():
                return await asyncio.wrap_future(
                    loop.run_in_executor(
                        None,
                        functools.partial(
                            run_agent,
                            user_text_for_agent,
                            companion.profile.model_dump(),
                            companion.state.model_dump(),
                            memory_text,
                            "",
                            language,
                            current_time,
                            user_gender,
                        ),
                    )
                )

            agent_task = asyncio.create_task(_run_agent_coro())
            keep_task = asyncio.create_task(
                _keepalive_during_agent(websocket, agent_task, language)
            )
            t_agent = time.time()
            try:
                result = await asyncio.wait_for(asyncio.shield(agent_task), timeout=120.0)
                logger.info("[TIMING] agent_task (async): %.2fs", time.time() - t_agent)
            except asyncio.TimeoutError:
                try:
                    err_txt = _AGENT_TIMEOUT_MESSAGE.get(language, _AGENT_TIMEOUT_MESSAGE["zh"])
                    await websocket.send_text(json.dumps({"type": "error", "text": err_txt}))
                except Exception:
                    pass
                continue
            finally:
                keep_task.cancel()
                try:
                    await keep_task
                except asyncio.CancelledError:
                    pass

            response_text = result["response"]
            response_len = len(response_text)

            # 首条气泡前延迟（优化：缩短到最多1.5秒）
            if has_leave_intent:
                pre_delay = random.uniform(0.3, 1.0)
            else:
                pre_delay = min(1.5, max(0.1, response_len * 0.008) + random.uniform(0, 0.4))
            await asyncio.sleep(pre_delay)

            aff = float(getattr(companion.state, "affection", 0) or 0)
            show_think = random.random() < _THINK_TOAST_UI_PROBABILITY * (
                0.35 + 0.65 * min(1.0, aff / 100.0)
            )
            t_delivery = time.time()
            sent_segments, was_interrupted = await _deliver_assistant_content(
                websocket,
                companion,
                response_text,
                message_queue=message_queue,
                show_think_toasts=show_think,
                delivery_mood=delivery_mood,
                user_short_term=user_short_term,
            )
            logger.info("[TIMING] deliver_content: %.2fs, segments=%d, interrupted=%s", time.time() - t_delivery, len(sent_segments), was_interrupted)

            if was_interrupted:
                # 用户中途回复了新消息，已发送的部分已逐段保存
                # 即使被打断，仍更新已发送内容对应的状态（保持 AI 状态不 stale）
                if sent_segments:
                    companion.state.mood = result["mood"]
                    companion.state.affection = result["affection"]
                    companion.state.turns += 1
                    if result.get("new_facts"):
                        companion.memory.facts.add_facts(result["new_facts"])
                    if companion.memory.summary.should_update() and result.get("new_summary"):
                        companion.memory.summary.update(result["new_summary"])
                        companion.state.summary = result["new_summary"]
                    else:
                        companion.state.summary = companion.memory.summary.get_summary()
                    companion.save_state(user_id=user_id)
                continue

            # 完整发送后，保存回合和状态（逐段已存入 short_term）
            # 使用实际发送给用户的去括号内容保存记忆，保持与用户体验一致
            actual_response = "\n\n".join(sent_segments)
            companion.memory.commit_turn(combined_user_plain, actual_response)

            companion.state.mood = result["mood"]
            companion.state.affection = result["affection"]
            companion.state.turns += 1

            # 每5轮触发人格进化（独立调用）
            if companion.state.turns > 0 and companion.state.turns % 5 == 0:
                try:
                    from services.agent import evolve_persona
                    evolved = evolve_persona(
                        companion.profile.model_dump(),
                        companion.state.model_dump(),
                        memory_text,
                        language,
                    )
                    if evolved.get("evolved_personality"):
                        companion.state.evolved_personality = evolved["evolved_personality"]
                    if evolved.get("evolved_background"):
                        companion.state.evolved_background = evolved["evolved_background"]
                    if evolved.get("evolved_speech_style"):
                        companion.state.evolved_speech_style = evolved["evolved_speech_style"]
                except Exception as e:
                    logger.warning("Persona evolve failed: %s", e)

            if result.get("new_facts"):
                companion.memory.facts.add_facts(result["new_facts"])

            if companion.memory.summary.should_update() and result.get("new_summary"):
                companion.memory.summary.update(result["new_summary"])
                companion.state.summary = result["new_summary"]
            else:
                companion.state.summary = companion.memory.summary.get_summary()

            companion.save_state(user_id=user_id)

            # AI 回复完成后，启动新的主动消息定时任务
            proactive_task = asyncio.create_task(
                _send_proactive_message(websocket, companion, language, companion_id, user_short_term=user_short_term)
            )

    except WebSocketDisconnect:
        if proactive_task and not proactive_task.done():
            proactive_task.cancel()
        prev_sess = get_session(companion_id)
        prev_lang = prev_sess.get("lang")
        next_sess = {**prev_sess, "lang": prev_lang or user_lang or "zh"}
        next_sess.pop("pending_retention", None)
        next_sess.pop("last_disconnect", None)
        await set_session(companion_id, next_sess)
    except Exception:
        logger.exception("WebSocket chat loop failed for companion %s", companion_id)
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                lk = normalize_ui_language(get_session(companion_id).get("lang") or "zh")
                msg = _WS_CHAT_UNEXPECTED_ERROR.get(lk, _WS_CHAT_UNEXPECTED_ERROR["zh"])
                await websocket.send_text(json.dumps({"type": "error", "text": msg}))
        except Exception as send_err:
            logger.warning(
                "Failed to send websocket error payload for companion %s: %s",
                companion_id,
                send_err,
            )
    finally:
        if proactive_task and not proactive_task.done():
            proactive_task.cancel()
            try:
                await proactive_task
            except asyncio.CancelledError:
                pass
        if not receiver_task.done():
            receiver_task.cancel()
        try:
            await receiver_task
        except asyncio.CancelledError:
            pass


@router.post("/knowledge/search",tags=["知识库"], summary="搜索知识库" )
async def public_knowledge_search(data: dict):
    """公开知识库搜索（发现页用）"""
    from services.knowledge_base import knowledge_base
    query = data.get("query", "")
    top_k = data.get("top_k", 10)
    if not query:
        return {"results": []}
    results = knowledge_base.search_entries(query, top_k=top_k)
    return {"results": results}
