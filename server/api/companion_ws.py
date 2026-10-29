import asyncio
import functools
import json
import logging
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, SystemMessage
from starlette.websockets import WebSocketState

from core.auth import verify_token as verify_user_token, verify_ws_ticket
from core.concurrency import agent_semaphore, get_user_lock, heavy_load_ratio, RedisTurnLock
from core.agent_queue import AgentJob, enqueue_agent_job, new_job_id, queue_enabled, wait_agent_result
from core.database import get_db
from core.executor import AGENT_POOL
from core.rate_limit import check_chat_rate_limit
from core.ws_delivery import queue_has_user_interrupt, ws_session_key
from core.config import (
    _AGENT_TIMEOUT_MESSAGE,
    _DUPLICATE_USER_MESSAGE,
    _MULTI_MESSAGE_AGENT_PREFIX,
    _QUEUE_COALESCED_MESSAGE,
    detect_leave_intent,
    split_response,
)
from core.state import get_companion_manager, get_session, set_session
from services.agent_utils import build_dialogue_time_context, build_system_prompt, get_content_restriction, llm_content_to_str
from services.agent_runner import run_agent, run_memory_update_async
from services.agent import get_llm
from services.llm.client import llm_invoke
from services.memory import llm_visible_messages, normalize_message_text_for_dedup
from services.prompts.resolve import get_pack, resolve_lang
from core.i18n import (
    _,
    normalize_ui_language,
    _WS_ACCESS_DENIED,
    _WS_AUTH_FAILED,
    _WS_CHAT_UNEXPECTED_ERROR,
    _WS_COMPANION_NOT_FOUND,
    _WS_COMPANION_SOFT_DELETED,
)
from services.companion_manager import hydrate_user_affection_turns
from services.relation_card import (
    RELATION_CARD_ENABLED,
    RELATION_CARD_FALLBACK_FULL_MEMORY,
    is_card_empty,
    load_card,
    merge_facts,
    render_card,
    save_card,
    touch_state,
)
from services.dialogue_phase2 import (
    merge_open_threads,
    proactive_should_send,
    push_deny_fingerprints,
    push_deny_hook,
    resolve_relation_stage,
    seed_deny_from_recent,
)
from services.archive_queue import enqueue_archive_job, archive_queue_enabled
from services.archive_process import process_archive_job
import time as _time_mod

from api.companion_access import _assert_companion_user_access

router = APIRouter(tags=["聊天"])
logger = logging.getLogger(__name__)
_chat_turn_logger = logging.getLogger("chat.turn")


def _companion_pronoun(companion) -> str:
    g = (getattr(companion.profile, "gender", None) or "").strip().lower()
    if g in ("male", "男", "m"):
        return "他"
    if g in ("female", "女", "f"):
        return "她"
    return "TA"


def _log_chat_turn(payload: dict) -> None:
    try:
        _chat_turn_logger.info("%s", json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


# 单次从 WS 队列合并处理的用户消息条数上限：避免一次合并过多导致模型/记忆语义「串」、且 empty() 在竞态下不可靠
_WS_BURST_MAX_MESSAGES = 10


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


# 空闲 WS 应用层心跳：须明显小于 nginx proxy_read_timeout（现为 180s）
_WS_IDLE_HEARTBEAT_MIN_S = float(os.getenv("WS_IDLE_HEARTBEAT_MIN_S", "25") or 25)
_WS_IDLE_HEARTBEAT_MAX_S = float(os.getenv("WS_IDLE_HEARTBEAT_MAX_S", "45") or 45)
_WS_PROACTIVE_SLEEP_CHUNK_S = float(os.getenv("WS_PROACTIVE_SLEEP_CHUNK_S", "30") or 30)
# 默认不因缺少 JSON pong 杀线（仅靠下行 ping 撑代理；发送失败才结束连接）
# 设 WS_REQUIRE_PONG=1 时恢复「超时无 pong → force-close」
_WS_REQUIRE_PONG = _env_flag("WS_REQUIRE_PONG", "0")
# 仅当 WS_REQUIRE_PONG=1 生效；默认 180s，避免客户端偶发未回 pong 就被掐断
_WS_PONG_TIMEOUT_S = float(os.getenv("WS_PONG_TIMEOUT_S", "180") or 180)
# 后台清扫间隔：扫描无 pong 应答的注册连接（REQUIRE_PONG=0 时仅收尸已断开连接）
_WS_ZOMBIE_SWEEP_S = float(os.getenv("WS_ZOMBIE_SWEEP_S", "30") or 30)
# 是否额外发 ASGI websocket.ping（部分代理对 control frame 更稳）；失败忽略
_WS_PROTOCOL_PING = _env_flag("WS_PROTOCOL_PING", "1")

_DELIVERY_DELAY_FACTOR = float(os.getenv("DELIVERY_DELAY_FACTOR", "0.6"))

# connection_id -> WebSocket；供僵尸清扫主动 close，释放协程与连接槽位
_ws_live_registry: Dict[str, WebSocket] = {}
# user_id:companion_id -> 当前连接；旧连接生成完成后可转投到新页的 WS
_ws_session_registry: Dict[str, WebSocket] = {}
_ws_janitor_lock = asyncio.Lock()
_ws_janitor_task: Optional[asyncio.Task] = None
_ws_main_loop: Optional[asyncio.AbstractEventLoop] = None
# 旧连接已断时，这些帧仍应转投到同一用户+智能体的新连接（不含 ping/typing）
_WS_FAILOVER_TYPES = frozenset({"message", "system", "error", "need_vip", "our_moments"})


def _ws_session_key(user_id: int, companion_id: str) -> str:
    return ws_session_key(user_id, companion_id)


def _ws_heartbeat_interval() -> float:
    lo = max(5.0, min(_WS_IDLE_HEARTBEAT_MIN_S, _WS_IDLE_HEARTBEAT_MAX_S))
    hi = max(lo, _WS_IDLE_HEARTBEAT_MAX_S)
    return random.uniform(lo, hi)


def _ws_mark_alive(websocket: WebSocket) -> None:
    """任意入站帧（含 pong / 业务消息）刷新存活时间。"""
    now = time.monotonic()
    setattr(websocket, "_trandsai_last_pong_at", now)
    setattr(websocket, "_trandsai_awaiting_pong", False)
    uid = getattr(websocket, "_trandsai_user_id", None)
    cid = getattr(websocket, "_trandsai_companion_id", None)
    if uid is not None and cid:
        try:
            from services.im_ws_bridge import mark_user_session_online

            mark_user_session_online(int(uid), str(cid))
        except Exception:
            pass


def _ws_note_ping_sent(websocket: WebSocket) -> None:
    now = time.monotonic()
    setattr(websocket, "_trandsai_ping_sent_at", now)
    # 不要求 pong 时不进入 awaiting，避免误判僵死
    setattr(websocket, "_trandsai_awaiting_pong", bool(_WS_REQUIRE_PONG))


def _ws_is_zombie(websocket: WebSocket) -> bool:
    """已发 ping 且超过 WS_PONG_TIMEOUT_S 仍无 pong/入站 → 僵死。"""
    if not _WS_REQUIRE_PONG:
        return False
    if not getattr(websocket, "_trandsai_awaiting_pong", False):
        return False
    ping_at = float(getattr(websocket, "_trandsai_ping_sent_at", 0) or 0)
    if ping_at <= 0:
        return False
    return (time.monotonic() - ping_at) >= max(30.0, _WS_PONG_TIMEOUT_S)


def _ws_awaiting_pong_age_s(websocket: WebSocket) -> float:
    ping_at = float(getattr(websocket, "_trandsai_ping_sent_at", 0) or 0)
    if ping_at <= 0:
        return 0.0
    return max(0.0, time.monotonic() - ping_at)


async def _ws_force_close(websocket: WebSocket, *, reason: str = "zombie") -> None:
    """主动关闭失效 WS，释放 Nginx/Uvicorn 连接槽。"""
    try:
        last_pong = float(getattr(websocket, "_trandsai_last_pong_at", 0) or 0)
        last_pong_age = (time.monotonic() - last_pong) if last_pong > 0 else -1.0
        logger.info(
            "WS force-close (%s) state=%s | conn=%s user=%s companion=%s | "
            "require_pong=%s awaiting=%s awaiting_age=%.1fs last_pong_age=%.1fs",
            reason,
            getattr(websocket, "client_state", None),
            getattr(websocket, "_trandsai_connection_id", None),
            getattr(websocket, "_trandsai_user_id", None),
            getattr(websocket, "_trandsai_companion_id", None),
            _WS_REQUIRE_PONG,
            bool(getattr(websocket, "_trandsai_awaiting_pong", False)),
            _ws_awaiting_pong_age_s(websocket),
            last_pong_age,
        )
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1001)
    except Exception as e:
        logger.debug("WS force-close ignored: %s", e)


async def _ws_try_protocol_ping(websocket: WebSocket) -> None:
    """可选 ASGI 层 websocket.ping；不支持或失败时静默忽略。"""
    if not _WS_PROTOCOL_PING:
        return
    try:
        await websocket.send({"type": "websocket.ping", "bytes": b""})
    except Exception as e:
        logger.debug("WS protocol ping skipped: %s", e)


def _ws_register(connection_id: str, websocket: WebSocket) -> None:
    _ws_live_registry[connection_id] = websocket
    now = time.monotonic()
    setattr(websocket, "_trandsai_connection_id", connection_id)
    setattr(websocket, "_trandsai_last_pong_at", now)
    setattr(websocket, "_trandsai_awaiting_pong", False)
    setattr(websocket, "_trandsai_ping_sent_at", 0.0)


def _ws_bind_session(websocket: WebSocket, user_id: int, companion_id: str) -> None:
    """把连接挂到 (user, companion) 上，便于旧连接断开后把回复转到新 WS。"""
    setattr(websocket, "_trandsai_user_id", int(user_id))
    setattr(websocket, "_trandsai_companion_id", companion_id)
    _ws_session_registry[_ws_session_key(user_id, companion_id)] = websocket
    try:
        from services.im_ws_bridge import mark_user_session_online

        mark_user_session_online(int(user_id), str(companion_id))
    except Exception:
        pass


def _ws_bind_memory(websocket: WebSocket, companion, user_id: int) -> None:
    """连接级绑定按用户隔离的记忆，避免共享 Companion.memory.bind_user 串写。"""
    mem = companion.memory_for(int(user_id))
    setattr(websocket, "_trandsai_memory", mem)


def _ws_user_memory(websocket: WebSocket, companion=None):
    """取当前连接的用户记忆；缺失时按 WS 上的 user_id 重建。"""
    mem = getattr(websocket, "_trandsai_memory", None)
    if mem is not None:
        return mem
    if companion is None:
        return None
    uid = getattr(websocket, "_trandsai_user_id", None)
    if uid is None:
        return companion.memory
    mem = companion.memory_for(int(uid))
    setattr(websocket, "_trandsai_memory", mem)
    return mem


def _ws_clear_session_online(websocket: WebSocket) -> None:
    """断线立刻清会话在线标记，避免 AI 仍在生成时被当成「在聊天页」而不计未读。"""
    uid = getattr(websocket, "_trandsai_user_id", None)
    cid = getattr(websocket, "_trandsai_companion_id", None)
    if uid is None or not cid:
        return
    key = _ws_session_key(int(uid), str(cid))
    if _ws_session_registry.get(key) is websocket:
        _ws_session_registry.pop(key, None)
        try:
            from services.im_ws_bridge import mark_user_session_offline

            mark_user_session_offline(int(uid), str(cid))
        except Exception:
            pass


def _ws_unregister(connection_id: str) -> None:
    ws = _ws_live_registry.pop(connection_id, None)
    if ws is None:
        return
    _ws_clear_session_online(ws)


def _note_unread_after_assistant_persist(
    websocket: WebSocket,
    *,
    delivered_live: bool,
) -> None:
    """助手消息已落库：若用户当前不在该会话 WS，则 +未读并推 inbox。"""
    uid = getattr(websocket, "_trandsai_user_id", None)
    cid = getattr(websocket, "_trandsai_companion_id", None)
    if not uid or not cid:
        return
    try:
        from services.user_chat_unread import note_assistant_unread

        note_assistant_unread(int(uid), str(cid), delivered_live=bool(delivered_live))
    except Exception:
        logger.debug(
            "user unread after assistant persist skipped user=%s companion=%s",
            uid,
            cid,
            exc_info=True,
        )


async def _ws_janitor_loop() -> None:
    """定时扫描已断开连接；WS_REQUIRE_PONG=1 时额外清理无 pong 僵死连接。"""
    while True:
        try:
            await asyncio.sleep(max(10.0, _WS_ZOMBIE_SWEEP_S))
            dead: List[str] = []
            for cid, ws in list(_ws_live_registry.items()):
                try:
                    if ws.client_state != WebSocketState.CONNECTED:
                        dead.append(cid)
                        continue
                    if _ws_is_zombie(ws):
                        await _ws_force_close(ws, reason="janitor-no-pong")
                        dead.append(cid)
                except Exception:
                    dead.append(cid)
            for cid in dead:
                _ws_unregister(cid)
            if dead:
                logger.warning(
                    "WS janitor closed %s connection(s); live=%s require_pong=%s",
                    len(dead),
                    len(_ws_live_registry),
                    _WS_REQUIRE_PONG,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WS janitor loop error")


async def _ensure_ws_janitor() -> None:
    global _ws_janitor_task, _ws_main_loop
    async with _ws_janitor_lock:
        _ws_main_loop = asyncio.get_running_loop()
        if _ws_janitor_task is not None and not _ws_janitor_task.done():
            return
        _ws_janitor_task = asyncio.create_task(_ws_janitor_loop(), name="ws-zombie-janitor")


def is_user_chat_connected(user_id: int, companion_id: str) -> bool:
    """本进程是否有该用户对该智能体的聊天 WS。"""
    if not user_id or not companion_id:
        return False
    ws = _ws_session_registry.get(_ws_session_key(int(user_id), str(companion_id)[:8]))
    if ws is None:
        return False
    try:
        return ws.client_state == WebSocketState.CONNECTED
    except Exception:
        return False


async def deliver_assistant_message_local(
    user_id: int, companion_id: str, text: str, image_url: str = ""
) -> bool:
    """向本进程内用户 IM 连接推送 assistant 帧（供本机与 Redis 桥调用）。"""
    from services.strategy_image import assistant_message_payload

    url = (image_url or "").strip()
    body = (text or "").strip()
    if not user_id or not companion_id or (not body and not url):
        return False
    ws = _ws_session_registry.get(_ws_session_key(int(user_id), str(companion_id)))
    if ws is None:
        return False
    return await _ws_send_json(ws, assistant_message_payload(body, url))


async def deliver_typing_local(user_id: int, companion_id: str) -> bool:
    """向本进程内用户 IM 连接推送 typing 帧。"""
    if not user_id or not companion_id:
        return False
    ws = _ws_session_registry.get(_ws_session_key(int(user_id), str(companion_id)[:8]))
    if ws is None:
        return False
    return await _ws_send_json(ws, {"type": "typing"})


def try_push_typing(user_id: int, companion_id: str) -> bool:
    """同步侧尽力推 typing：本机投递 + Redis 广播其它 worker（与正常聊天同帧）。"""
    if not user_id or not companion_id:
        return False
    uid = int(user_id)
    cid = str(companion_id)[:8]
    local_ok = False
    ws = _ws_session_registry.get(_ws_session_key(uid, cid))
    payload = {"type": "typing"}
    loop = _ws_main_loop
    if ws is not None and loop is not None and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_ws_send_json(ws, payload), loop)
            local_ok = True
        except Exception:
            local_ok = False
    try:
        from services.im_ws_bridge import publish_user_typing

        publish_user_typing(uid, cid)
    except Exception:
        pass
    return local_ok


def try_push_assistant_message(
    user_id: int, companion_id: str, text: str, image_url: str = ""
) -> bool:
    """同步侧尽力推助手消息：本机投递 + Redis 广播其它 worker。

    返回 True：本机已调度发送，或 Redis 标记该会话仍在线（跨 worker 可达）。
    """
    from services.strategy_image import assistant_message_payload

    url = (image_url or "").strip()
    body = (text or "").strip()
    if not user_id or not companion_id or (not body and not url):
        return False
    uid = int(user_id)
    cid = str(companion_id)[:8]
    local_ok = False
    ws = _ws_session_registry.get(_ws_session_key(uid, cid))
    payload = assistant_message_payload(body, url)
    loop = _ws_main_loop
    if ws is not None and loop is not None and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_ws_send_json(ws, payload), loop)
            local_ok = True
        except Exception:
            local_ok = False
    try:
        from services.im_ws_bridge import is_user_session_online, publish_user_assistant

        publish_user_assistant(uid, cid, body, image_url=url)
        peer_online = is_user_session_online(uid, cid)
    except Exception:
        peer_online = False
    delivered = bool(local_ok or peer_online)
    try:
        from services.user_chat_unread import note_assistant_unread

        note_assistant_unread(uid, cid, delivered_live=delivered)
    except Exception:
        logger.debug(
            "user unread note skipped user=%s companion=%s",
            uid,
            cid,
            exc_info=True,
        )
    return delivered


async def _ws_try_send(websocket: WebSocket, payload: dict) -> bool:
    """向指定连接串行发送 JSON 帧。"""
    lock = getattr(websocket, "_trandsai_send_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(websocket, "_trandsai_send_lock", lock)
    try:
        async with lock:
            if websocket.client_state != WebSocketState.CONNECTED:
                return False
            await websocket.send_text(json.dumps(payload))
            return True
    except Exception:
        return False


async def _ws_send_json(websocket: WebSocket, payload: dict) -> bool:
    """发送 JSON；原连接已断时，把消息类帧转投到同一会话的新连接。"""
    if await _ws_try_send(websocket, payload):
        return True
    if payload.get("type") not in _WS_FAILOVER_TYPES:
        return False
    uid = getattr(websocket, "_trandsai_user_id", None)
    cid = getattr(websocket, "_trandsai_companion_id", None)
    if uid is None or not cid:
        return False
    alt = _ws_session_registry.get(_ws_session_key(int(uid), str(cid)))
    if alt is None or alt is websocket:
        return False
    return await _ws_try_send(alt, payload)


async def _ws_send_ping_or_close(websocket: WebSocket) -> bool:
    """
    空闲心跳：下发 JSON ping（可选协议 ping）撑住代理读超时。
    WS_REQUIRE_PONG=1 且上一轮超时无 pong 时关闭僵死连接。
    返回 False 表示应结束主循环（发送失败或主动杀僵死）。
    """
    if _ws_is_zombie(websocket):
        await _ws_force_close(websocket, reason="missed-pong")
        return False
    await _ws_try_protocol_ping(websocket)
    if not await _ws_send_json(websocket, {"type": "ping"}):
        return False
    _ws_note_ping_sent(websocket)
    return True


async def _sleep_with_ws_keepalive(
    websocket: WebSocket, total_seconds: float, *, chunk_seconds: Optional[float] = None
) -> bool:
    """
    长等待切成短片断。片间不再额外 ping（主循环空闲心跳已覆盖），仅检测连接仍可用。
    返回 False 表示连接已不可用。
    """
    remaining = max(0.0, float(total_seconds))
    chunk = float(chunk_seconds if chunk_seconds is not None else _WS_PROACTIVE_SLEEP_CHUNK_S)
    chunk = max(5.0, chunk)
    while remaining > 0:
        step = min(chunk, remaining)
        await asyncio.sleep(step)
        remaining -= step
        if remaining <= 0:
            break
        if websocket.client_state != WebSocketState.CONNECTED:
            return False
        if _ws_is_zombie(websocket):
            await _ws_force_close(websocket, reason="proactive-missed-pong")
            return False
    return websocket.client_state == WebSocketState.CONNECTED


# 带括号「思考」时，仅约本概率的轮次向客户端发送 💭 toast（约每 10 次用户-AI 交互 1～2 次展示）
_THINK_TOAST_UI_PROBABILITY = 0.15

# 极短用户文不参与「重复发送」去重，避免「在吗」连发被拦
_DUPLICATE_CHECK_MIN_LEN = 4
# 连点防抖：相同正文在此秒数内视为重复（含短句），静默跳过、不回伪角色忙线
_CHAT_DEBOUNCE_S = float(os.getenv("CHAT_DEBOUNCE_S", "2.0") or 2.0)

# 半角 () 与全角（）：整段匹配，用于在全文上按顺序切出「思考块」与「正文块」
_BRACKET_BLOCK_PATTERN = re.compile(r"\([^)]*\)|（[^）]*）")
# 去掉括号块（供正文条内二次清理）
_BRACKET_STRIP_PATTERN = re.compile(r"\([^)]*\)|（[^）]*）")
_THINK_INNER_MAX_RAW = 6000
_THINK_TOAST_WS_MAX = 2400

# 所有可能的思考过程字段名（中英日韩葡西印尼 + prompt 标签 + JSON 字段名）
_THINK_FIELD_NAMES = (
    # 中文（inner 节点 + respond 节点）
    "思考|反思|创意|情绪|亲密度信号|威胁等级|极端|手段模式|策略|人设依据"
    "|意图|必须回应|新角度|避免|分析|判断|计划|观察|推理"
    "|理解要点|本轮撩法|创意提示|用户意图|指代|承接"
    # 英文（含 thought/reflection/creativity 等模型常泄字段）
    "|understanding|flirt move|creative hint|user intent|must answer|output"
    "|think|thought|reflect|reflection|creative|creativity|emotion|mood|intent|plan|reason|analysis"
    "|avoid|observation|strategy|inner|aside|reasoning|brainstorm"
    # 日文
    "|反省|創造|創意ヒント|今ターンのイチャつき|ユーザー意図|必ず返す点"
    # 韩文
    "|이해 포인트|이번 플러팅|창의 힌트|사용자 의도|반드시 답할 점"
    "|사고|반성|창의|감정|위협|전략"
    # 葡萄牙文
    "|entendimento|flerte desta vez|dica criativa|intenção do usuário|deve responder"
    "|pensamento|reflexão|criativo|emocao"
    # 西班牙文
    "|comprensión|coqueteo de este turno|pista creativa|intención del usuario|debe responder"
    "|pensamiento|reflexión|creativo|emocion"
    # 印尼文
    "|poin pemahaman|gerakan flirt giliran ini|petunjuk kreatif|niat pengguna|wajib dijawab"
    "|berpikir|refleksi|kreatif|emosi"
    # v2 JSON 字段名
    "|user_intent|must_answer|refs|flirt_move|creative_hint|affection_signal"
)

# JSON 格式：从 {"思考":"...","输出":"...","创意":"..."} 中只提取 "输出" 字段
# 值内允许真实换行（LLM 返回的 JSON 值可能含换行）
_JSON_OUTPUT_PATTERN = re.compile(
    r'\{[^{}]*"(?:输出|output|reply|response)"\s*:\s*"(.*?)"\s*[^{}]*\}',
    re.DOTALL | re.IGNORECASE
)

# 纯文本格式：提取 "输出：xxx" 后面的内容（到下一个已知字段或结尾）
_TEXT_OUTPUT_PATTERN = re.compile(
    r'(?:输出|output|reply|response)[：:]\s*(.+?)(?=\n\s*(?:' + _THINK_FIELD_NAMES + r')[：:]|\Z)',
    re.DOTALL | re.IGNORECASE
)

# JSON 键值对格式：匹配 "字段名": "值", 或 "字段名": "值"（含多行值）
_JSON_KV_PATTERN = re.compile(
    r'"\s*(?:' + _THINK_FIELD_NAMES + r')"\s*:\s*".*?"\s*,?\s*',
    re.DOTALL | re.IGNORECASE
)

# 纯文本格式：剥离所有已知思考字段行（兜底用）
_THINK_LINES_PATTERN = re.compile(
    r'^\s*(?:' + _THINK_FIELD_NAMES + r')[：:]\s*.*$',
    re.MULTILINE | re.IGNORECASE
)

# prompt 标签格式：剥离 【xxx】开头的行（如 【理解要点】xxx 【回复】xxx）
_BRACKET_LABEL_PATTERN = re.compile(
    r'^\s*【(?:' + _THINK_FIELD_NAMES + r'|输出|output)】\s*.*$',
    re.MULTILINE | re.IGNORECASE
)

# JSON 格式：匹配完整 JSON 对象（含任意已知思考字段，跨行）
_JSON_THINK_BLOCK_PATTERN = re.compile(
    r'\{[^{}]*(?:"(?:' + _THINK_FIELD_NAMES + r')")[^{}]*\}',
    re.DOTALL | re.IGNORECASE
)


def _strip_json_think_blocks(text: str) -> str:
    """从 LLM 返回中只提取「输出」字段，丢弃所有思考过程。
    优先级：JSON 输出字段 → 纯文本输出字段 → 剥离 JSON KV → 剥离思考行 → 原文"""
    if not text:
        return text

    # 1. JSON 格式：提取 "输出" 字段值
    m = _JSON_OUTPUT_PATTERN.search(text)
    if m:
        return m.group(1).replace('\\"', '"').replace('\\n', '\n').strip()

    # 2. 纯文本格式：提取 "输出：" 后面的内容
    m = _TEXT_OUTPUT_PATTERN.search(text)
    if m:
        return m.group(1).strip()

    # 3. 兜底：先尝试匹配整个 JSON 思考块（避免 KV 先剥离导致外层花括号残留），再剥离残余 KV + 纯文本思考字段行 + 【标签】行
    cleaned = _JSON_THINK_BLOCK_PATTERN.sub("", text)
    cleaned = _JSON_KV_PATTERN.sub("", cleaned)
    cleaned = _THINK_LINES_PATTERN.sub("", cleaned)
    cleaned = _BRACKET_LABEL_PATTERN.sub("", cleaned)
    # 残留碎渣：单独的 { }、空行、仍像 "key": 的行
    cleaned = re.sub(r'^\s*[{}\[\],]+\s*$', "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(
        r'^\s*"?\s*(?:thought|reflection|creativity|think|reflect|creative|avoid|reasoning)\s*"?\s*[:：].*$',
        "",
        cleaned,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if cleaned and not _looks_like_think_leak(cleaned):
        return cleaned

    # 4. 仍像泄漏：尝试取最后一段像口语的行
    spoken = _extract_spoken_tail(text)
    if spoken:
        return spoken

    # 5. 全部被剥离或原文无思考字段
    return (cleaned or text).strip()


def _looks_like_think_leak(text: str) -> bool:
    """正文仍像内部 JSON/思考字段泄漏。"""
    t = (text or "").strip()
    if not t:
        return False
    if re.search(
        r'^\s*"?(thought|reflection|creativity|think|reflect|creative)"?\s*:',
        t,
        re.I | re.M,
    ):
        return True
    if t.count('"') >= 4 and ":" in t and len(t) < 800:
        keys = len(re.findall(r'"[^"]{2,40}"\s*:', t))
        if keys >= 2:
            return True
    return False


def _extract_spoken_tail(text: str) -> str:
    """从混杂输出中尽量抽出最后一段口语正文。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    keep: list[str] = []
    for ln in lines:
        if re.match(
            r'^"?\s*(?:thought|reflection|creativity|think|reflect|creative|avoid|reasoning|理解要点|本轮撩法)\s*"?\s*[:：]',
            ln,
            re.I,
        ):
            continue
        if re.match(r'^[{}\[\],]+$', ln):
            continue
        if ln in ("...", "…", "{", "}", "[]"):
            continue
        keep.append(ln)
    if not keep:
        return ""
    # 优先取不含 JSON 键风格的连续尾部
    spoken: list[str] = []
    for ln in reversed(keep):
        if re.search(r'"\s*:\s*"', ln) or re.match(r'^"[^"]+"\s*:', ln):
            if spoken:
                break
            continue
        spoken.append(ln)
    spoken.reverse()
    out = "\n".join(spoken).strip()
    return out if len(out) >= 2 else ""

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
    """少切气泡：仅在「句末 + 空白 + 下一句」时换行；不再对紧挨着的句号后强制切分。

    REQ-A7：无条间 sleep；减少机枪式短气泡。
    """
    if not (s or "").strip():
        return s or ""
    # 仅保留有空白的软换行；去掉 tight break（句号后立刻切条）
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
) -> Tuple[str, bool]:
    """仅发送正文气泡（已不含括号）。与上条去重后相同则一般不发；极短句或约 12% 长句仍发出（拟人「嘴笨重复」）。
    返回 (应落库正文, 是否已成功推到聊天 WS)。发送失败仍返回正文供落库，避免用户离页后回复丢失。"""
    display_text = (display_text or "").strip()
    if not display_text:
        return "", False
    skip = False
    mem = _ws_user_memory(websocket, companion)
    if mem is not None:
        last_a = mem.short_term.get_last_assistant_content()
        if last_a is not None:
            n_new = normalize_message_text_for_dedup(display_text)
            n_last = normalize_message_text_for_dedup(last_a)
            if n_new == n_last:
                if len(n_new) < 10:
                    skip = False
                else:
                    skip = random.random() < 0.88
    if skip:
        return "", False
    delivered = await _ws_send_json(
        websocket,
        {"type": "message", "role": "assistant", "text": display_text},
    )
    return display_text, bool(delivered)


async def _keepalive_during_agent(websocket: WebSocket, agent_task, language: str) -> None:
    """长推理时仅续传 typing（REQ-A7：无 filler）。"""
    n = 0
    while not agent_task.done():
        n += 1
        if not await _ws_send_json(websocket, {"type": "typing"}):
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


async def _wait_acquire_with_typing(
    websocket: WebSocket,
    waiter: "asyncio.Future",
    *,
    label: str = "wait",
) -> bool:
    """等待 Future 完成期间只打 typing，不对用户塞系统/角色文案。"""
    keep = asyncio.create_task(_keepalive_during_agent(websocket, waiter, ""))
    try:
        return bool(await waiter)
    finally:
        keep.cancel()
        try:
            await keep
        except asyncio.CancelledError:
            pass


async def _acquire_turn_lock_for_ws(
    websocket: WebSocket,
    user_id: int,
    companion_id: str,
) -> Tuple[Optional[object], bool, int]:
    """等上一轮 turn_lock；成功返回 (lock, True, wait_ms)。失败不向用户发「有点忙」。"""
    wait_s = float(os.getenv("TURN_LOCK_WAIT_S", "120") or 120)
    t0 = time.perf_counter()
    lock = await get_user_lock(user_id, companion_id)

    async def _do_acquire() -> bool:
        if isinstance(lock, RedisTurnLock):
            return await lock.acquire(timeout=wait_s)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=wait_s)
            return True
        except asyncio.TimeoutError:
            return False

    acq_task = asyncio.create_task(_do_acquire())
    try:
        ok = await _wait_acquire_with_typing(websocket, acq_task, label="turn_lock")
    except Exception:
        if not acq_task.done():
            acq_task.cancel()
            try:
                await acq_task
            except Exception:
                pass
        ok = False
    wait_ms = int((time.perf_counter() - t0) * 1000)
    if not ok:
        logger.info(
            "turn_lock wait exhausted user=%s companion=%s wait_ms=%s (no busy text to user)",
            user_id,
            companion_id,
            wait_ms,
        )
        return lock, False, wait_ms
    return lock, True, wait_ms


def _queue_has_user_interrupt(message_queue: Optional[asyncio.Queue]) -> bool:
    return queue_has_user_interrupt(message_queue)


async def _deliver_assistant_content(
    websocket: WebSocket,
    companion,
    response_text: str,
    *,
    message_queue: Optional[asyncio.Queue] = None,
    show_think_toasts: bool = False,
    delivery_mood: str = "neutral",
) -> Tuple[List[str], bool]:
    """严格按 `_split_thinks_and_text` 的顺序下发：每条 think 可发 toast（由 show_think_toasts 控制频次），再处理紧随的正文块；
    正文块内先软换行再 split_response；分条节奏由 delivery_mood（激动/平静/普通）影响 max_chars 与条间是否打 typing、暂停长短。
    不再推送 LangGraph think 节点原文。返回 (已持久化的正文条列表, 是否被队列中断)。"""
    sent_segments: list[str] = []
    # 先移除 JSON 格式的思考过程（思考/反思/创意），这些是内部推理不应展示给用户
    cleaned_text = _strip_json_think_blocks(response_text or "")
    parts = _split_thinks_and_text(cleaned_text)
    if not parts:
        return sent_segments, False

    bubble_idx = 0
    think_toast_sent = False  # 本轮若展示思考，仅发第一条 💭，避免同条回复内多条刷屏

    async def _interrupted() -> bool:
        return _queue_has_user_interrupt(message_queue)

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
                await _ws_send_json(
                    websocket, {"type": "toast", "text": f"💭 {inner}"}
                )
            except Exception:
                pass
            think_toast_sent = True
            continue

        # 正文块：去残留括号 → 收边碎渣 → 句间软换行 → 再按 split_response 分条
        body = _BRACKET_STRIP_PATTERN.sub("", payload).strip()
        if not body:
            continue
        try:
            from services.dialogue_quality import tidy_spoken_edges

            body = tidy_spoken_edges(body)
        except Exception:
            pass
        if not body:
            continue
        body = _inject_soft_linebreaks_for_mixed_messages(body)
        if delivery_mood == "excited":
            _mc = random.randint(160, 220)
        elif delivery_mood == "calm":
            _mc = random.randint(220, 320)
        else:
            _mc = random.randint(180, 260)
        subsegs = split_response(body, max_chars=_mc)
        for j, sub in enumerate(subsegs):
            if await _interrupted():
                return sent_segments, True
            sub_clean = _BRACKET_STRIP_PATTERN.sub("", (sub or "").strip()).strip()
            if not sub_clean:
                continue
            try:
                from services.dialogue_quality import tidy_spoken_edges

                sub_clean = tidy_spoken_edges(sub_clean)
            except Exception:
                pass
            if not sub_clean:
                continue
            # 丢弃过程泄漏/纯符号碎渣气泡（全语种）
            try:
                from services.dialogue_quality import (
                    _looks_process_leak,
                    _looks_internal_leak,
                    _script_char_count,
                )

                if _looks_process_leak(sub_clean) or _looks_internal_leak(sub_clean):
                    continue
                if _script_char_count(sub_clean) == 0:
                    continue
            except Exception:
                pass
            if bubble_idx > 0:
                # REQ-A7：无条间 sleep；仅可选 typing 提示
                skip_typing = False
                if delivery_mood == "excited":
                    skip_typing = random.random() < 0.48
                elif delivery_mood == "calm":
                    skip_typing = random.random() < 0.22
                else:
                    skip_typing = random.random() < 0.32
                if not skip_typing:
                    await _ws_send_json(websocket, {"type": "typing"})
                if await _interrupted():
                    return sent_segments, True
            stored = await _send_plain_assistant_bubble(websocket, companion, sub_clean)
            text, delivered = stored if isinstance(stored, tuple) else (stored, False)
            if text:
                mem = _ws_user_memory(websocket, companion)
                if mem is not None:
                    mem.add_assistant_message(text)
                sent_segments.append(text)
                _note_unread_after_assistant_persist(websocket, delivered_live=delivered)
            bubble_idx += 1

    return sent_segments, False


def _update_moment_messages(messages: List[dict], companion_id: str, user_id: int) -> None:
    """更新消息列表中 moment 类型消息的点赞状态"""
    from core.database import ShortTermMessageORM, MomentLikeORM

    for msg in messages:
        if msg.get("role") != "moment":
            continue

        try:
            content = msg.get("content")
            if not content:
                continue

            old_data = json.loads(content)
            moment_id = old_data.get("moment_id")
            if not moment_id:
                continue

            # 通过 moment_id 和 user_id 查询点赞状态
            liked = False
            if user_id:
                with get_db() as db:
                    like_record = db.query(MomentLikeORM).filter(
                        MomentLikeORM.moment_id == moment_id,
                        MomentLikeORM.user_id == user_id
                    ).first()
                    liked = like_record is not None

            # 如果点赞状态有变化，更新数据库存储并返回新内容
            if old_data.get("liked") != liked:
                new_data = {**old_data, "liked": liked}
                new_content = json.dumps(new_data, ensure_ascii=False)

                # 更新数据库
                msg_id = msg.get("id")
                if msg_id:
                    with get_db() as db:
                        db_msg = db.query(ShortTermMessageORM).filter(
                            ShortTermMessageORM.id == msg_id
                        ).first()
                        if db_msg:
                            db_msg.content = new_content

                # 更新返回给前端的内容
                msg["content"] = new_content

        except Exception as e:
            logger.warning("[Messages] 更新 moment 点赞状态失败: %s", e)


# ===== 主动消息相关 =====

def _companion_city_str(companion) -> Optional[str]:
    try:
        c = (companion.profile.city or "").strip()
        return c or None
    except Exception:
        return None


def _build_proactive_prompt(companion, lang: str, time_context: str, memory=None) -> str:
    """构建 AI 主动发消息的 prompt：必须贴着近聊续场，不突兀换题。"""
    from services.motive_layer import motive_block

    pack = get_pack(lang)
    ui = pack["respond_ui"]
    # 主动消息用人设瘦身版；初见按有效亲密度（广告 Context 已绑定时含托底）
    from services.affection_floor import effective_prompt_affection

    aff_eff = effective_prompt_affection(float(getattr(companion.state, "affection", 0) or 0))
    system_text = motive_block(lang) + "\n\n" + build_system_prompt(
        companion.profile.model_dump(),
        lang,
        turns=companion.state.turns,
        tier="respond",
        affection=aff_eff,
    )
    name = companion.profile.name
    mood = companion.state.mood
    mem = memory if memory is not None else companion.memory
    recent = llm_visible_messages(mem.short_term.get_recent(12))
    user_label = pack.get("user_role_label") or "User"
    recent_text = "\n".join(
        f"{user_label if m['role'] == 'user' else name}：{m['content']}"
        for m in recent
    )
    if not recent_text.strip():
        recent_text = "(no recent messages)"
    restriction_text = get_content_restriction(lang, aff_eff)
    ground = (
        "\n\n【续聊要求】主动消息必须承接上方近聊的情绪或未完话题；"
        "禁止忽然换成无关的画画/下雨/工作间隙开场；"
        "口语自然，像同一个人接着聊。"
        if pack_lang_is_zh(lang)
        else "\n\n[Continue the chat] Your proactive line must pick up the recent emotion/topic above; "
        "do not suddenly open with unrelated sketching/rain/work-break crumbs; "
        "sound like the same person continuing."
    )
    return f"""{system_text}

{ui['state'].format(mood=mood, affection=aff_eff)}

{pack['time_label']}
{time_context}

{ui['memory']}
{recent_text}
{ground}

{pack['proactive_task'].format(name=name)}

{restriction_text}"""


def pack_lang_is_zh(lang: str) -> bool:
    try:
        from services.prompts.resolve import pack_lang

        return pack_lang(lang) == "zh"
    except Exception:
        return (lang or "").lower().startswith("zh")


async def _send_proactive_message(websocket: WebSocket, companion, lang: str, companion_id: str, user_id: int = None):
    """定时发送主动消息（C3：信息量门槛）。"""
    try:
        from services.user_facing_lang import (
            REPLY_LANG_STICKY_KEY,
            recent_user_texts_from_messages,
            reply_conflicts_expected_lang,
            resolve_reply_lang,
            _LANG_DISPLAY,
        )
        from services.prompts.resolve import pack_lang
        from services.agent_utils import humanize

        mult = float(os.getenv("PROACTIVE_LOAD_MULTIPLIER", "1.0") or 1.0)
        if heavy_load_ratio() > 0.8:
            mult = max(mult, 2.0)
        wait_s = random.uniform(90, 180) * mult
        # 长等待切段 + ping，避免 nginx/proxy 空闲超时
        if not await _sleep_with_ws_keepalive(websocket, wait_s):
            return

        # 检查连接是否仍然打开
        if websocket.client_state == WebSocketState.DISCONNECTED:
            return

        sess = get_session(companion_id, user_id=user_id) or {}
        uid = sess.get("user_id")
        card = {}
        if RELATION_CARD_ENABLED and isinstance(uid, int):
            card = load_card(uid, companion_id)
        last_ts = None
        raw_ts = sess.get("last_user_ts")
        if raw_ts:
            try:
                last_ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            except Exception:
                last_ts = None
        ok, reason = proactive_should_send(
            card,
            affection=float(getattr(companion.state, "affection", 0) or 0),
            last_user_ts=last_ts,
        )
        if not ok:
            logger.info("Skip proactive (%s) companion=%s", reason, companion_id)
            return

        if not await _ws_send_json(websocket, {"type": "typing"}):
            return

        ui_lang = pack_lang(sess.get("lang") or lang or "en")
        history: list = []
        mem = None
        try:
            if isinstance(uid, int):
                mem = companion.memory_for(uid)
            from services.memory import MEMORY_RECENT_MESSAGES

            history = recent_user_texts_from_messages(
                llm_visible_messages(
                    (mem or companion.memory).short_term.get_recent(MEMORY_RECENT_MESSAGES)
                )
            )
        except Exception:
            history = []
        face_lang = resolve_reply_lang(
            current_text=None,
            history_user_texts=history,
            ui_lang=ui_lang,
            sticky=str(sess.get(REPLY_LANG_STICKY_KEY) or ""),
            channel="proactive",
        )["reply_lang"]

        time_ctx = build_dialogue_time_context(
            language=face_lang,
            tz_iana=(sess.get("client_tz") or "").strip() or None,
            tz_offset_minutes=sess.get("client_tz_offset"),
            companion_city=_companion_city_str(companion),
        )
        # 与主聊一致：广告户成人指引 / 托底走同一 ContextVar
        from services.affection_floor import bind_prompt_ads_user, reset_prompt_ads_user

        ads_token = bind_prompt_ads_user(user_id=uid if isinstance(uid, int) else user_id)
        try:
            prompt = _build_proactive_prompt(companion, face_lang, time_ctx, memory=mem)
            threads = card.get("open_threads") or []
            if threads:
                if face_lang.startswith("zh") or pack_lang(face_lang) == "zh":
                    prompt += f"\n\n【优先续话题】{threads[0]}\n不要只发空的「在干嘛」。"
                else:
                    prompt += (
                        f"\n\n[Prefer continuing] {threads[0]}\n"
                        "Don't send an empty 'what are you doing' check-in."
                    )
            pack = get_pack(face_lang)
            # Human：把近聊要点再塞一遍，推动动态接场
            recent_snip = ""
            try:
                if mem is not None:
                    last_msgs = mem.short_term.get_recent(6)
                    recent_snip = " | ".join(
                        (m.get("content") or "")[:80] for m in last_msgs if (m.get("content") or "").strip()
                    )[:400]
            except Exception:
                recent_snip = ""
            if pack_lang(face_lang) == "zh":
                human = (
                    "根据近聊写一条主动续聊消息，直接输出正文。"
                    + (f"\n近聊摘要：{recent_snip}" if recent_snip else "")
                )
            else:
                human = (
                    "Write one proactive message that continues the recent chat. Output the message only."
                    + (f"\nRecent snippets: {recent_snip}" if recent_snip else "")
                )
            llm = get_llm(role="respond")
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: llm_invoke(llm, [SystemMessage(content=prompt), HumanMessage(content=human)], node="proactive"),
            )
            text = llm_content_to_str(getattr(resp, "content", "")).strip()
            if not text:
                return

            # 成文后语种抽检：跟近史用户脚本冲突则改写一次，仍冲突则丢弃（不推西语蓝框）
            if reply_conflicts_expected_lang(text, face_lang):
                display = _LANG_DISPLAY.get(pack_lang(face_lang), face_lang)
                fix_human = (
                    human
                    + f"\n[Language] Rewrite the proactive line entirely in {display} only. "
                    "Continue the recent chat; spoken line only."
                )
                try:
                    resp2 = await loop.run_in_executor(
                        None,
                        lambda: llm_invoke(
                            llm,
                            [SystemMessage(content=prompt), HumanMessage(content=fix_human)],
                            node="proactive_lang_fix",
                        ),
                    )
                    text2 = llm_content_to_str(getattr(resp2, "content", "")).strip()
                    if text2:
                        text = text2
                except Exception as e:
                    logger.info("proactive lang fix failed companion=%s: %s", companion_id, e)
                if reply_conflicts_expected_lang(text, face_lang):
                    logger.info(
                        "Skip proactive lang-conflict companion=%s face=%s",
                        companion_id,
                        face_lang,
                    )
                    return
            try:
                text = humanize(text, face_lang)
            except Exception:
                pass
        finally:
            reset_prompt_ads_user(ads_token)

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
            )
        except Exception:
            return

        sess_done = get_session(companion_id, user_id=user_id) or {}
        uid = sess_done.get("user_id")
        companion.save_state(uid if isinstance(uid, int) else None)
    except Exception as e:
        logger.warning(
            "Proactive message generation failed for companion %s: %s",
            getattr(companion, "profile", None) and companion.profile.id,
            e,
        )


def _ws_payload_user_text(payload: dict) -> str:
    """从 WS JSON 取出用户原文。客户端可能用 text / content。"""
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "content"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            inner = raw.get("text") or raw.get("content")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


@router.websocket("/ws/chat/{companion_id}")
async def ws_chat(websocket: WebSocket, companion_id: str):
    companion_id = (companion_id or "").strip()[:8]
    # ===== DEBUG: 记录 WS 升级请求的完整细节 =====
    _dbg_ts = time.monotonic()
    _dbg_client = websocket.client  # (host, port) or None
    _dbg_headers = dict(websocket.headers)
    _dbg_query = dict(websocket.query_params)
    logger.info(
        "[WS-DEBUG] ▶ 升级请求到达 | companion=%s | client=%s | path=%s | query=%s | "
        "headers: origin=%s | upgrade=%s | connection=%s | sec-websocket-version=%s | "
        "sec-websocket-key=%s | user-agent=%s",
        companion_id,
        _dbg_client,
        websocket.url.path,
        {k: ("***" if k in ("ticket", "token") else v) for k, v in _dbg_query.items()},
        _dbg_headers.get("origin", "<无>"),
        _dbg_headers.get("upgrade", "<无>"),
        _dbg_headers.get("connection", "<无>"),
        _dbg_headers.get("sec-websocket-version", "<无>"),
        ("有" if _dbg_headers.get("sec-websocket-key") else "无"),
        _dbg_headers.get("user-agent", "<无>")[:120],
    )

    await websocket.accept()
    logger.info("[WS-DEBUG]   ✅ accept() 成功, 耗时 %.1fms", (time.monotonic() - _dbg_ts) * 1000)

    from services.version_prompt import log_app_version

    app_version = log_app_version(
        "ws_connect",
        headers=websocket.headers,
        query=websocket.query_params,
    )
    setattr(websocket, "_trandsai_app_version", app_version)

    # 连接级发送锁：主循环 ping / receive pong / proactive / typing 共用
    setattr(websocket, "_trandsai_send_lock", asyncio.Lock())

    ui_lang_early = normalize_ui_language(websocket.query_params.get("lang"))

    # 安全优化：优先 Header，其次 query token / 一次性 ticket（H5 兼容）
    token = (
        websocket.headers.get("x-token")
        or websocket.headers.get("X-Token")
        or websocket.query_params.get("token")
    )
    # ===== DEBUG: token 鉴权 =====
    _has_token = bool(token)
    logger.info("[WS-DEBUG]   token 鉴权: has_token=%s (来源: %s)",
                _has_token,
                "header" if (websocket.headers.get("x-token") or websocket.headers.get("X-Token")) else ("query" if websocket.query_params.get("token") else "无"))

    user_id = verify_user_token(token) if token else None
    logger.info("[WS-DEBUG]   token 验证结果: user_id=%s", user_id)

    if not user_id:
        ticket = (websocket.query_params.get("ticket") or "").strip()
        _ticket_len = len(ticket)
        _ticket_prefix = ticket[:8] + "..." if _ticket_len > 8 else ticket
        logger.info("[WS-DEBUG]   ticket 鉴权: has_ticket=%s, len=%s, prefix=%s",
                    bool(ticket), _ticket_len, _ticket_prefix)
        if ticket:
            user_id = verify_ws_ticket(ticket)
            logger.info("[WS-DEBUG]   ticket 验证结果: user_id=%s (Redis getdel 后)", user_id)
            if not user_id:
                # ticket 无效/过期/已消费——记录详细信息
                logger.warning(
                    "[WS-DEBUG]   ❌ ticket 失效! len=%s, prefix=%s | "
                    "可能原因: 已消费(一次性)/已过期(TTL=%ss)/Redis连接异常/从未写入",
                    _ticket_len, _ticket_prefix,
                    os.getenv("WS_TICKET_TTL", "60"),
                )

    if not user_id:
        logger.warning(
            "[WS-DEBUG]   🔒 鉴权失败, 将关闭连接 code=1008 | client=%s | companion=%s | "
            "has_token=%s | has_ticket=%s",
            _dbg_client, companion_id, _has_token, bool(_dbg_query.get("ticket")),
        )
        try:
            err = _WS_AUTH_FAILED.get(ui_lang_early, _WS_AUTH_FAILED["en"])
            await _ws_send_json(websocket, {"type": "error", "text": err})
            await websocket.close(code=1008)
        except Exception as _e:
            logger.warning("[WS-DEBUG]   close() 异常: %s", _e)
        return

    logger.info(
        "[WS-DEBUG]   🔓 鉴权通过 | user_id=%s | companion=%s | 耗时 %.1fms",
        user_id, companion_id, (time.monotonic() - _dbg_ts) * 1000,
    )
    try:
        from services.version_prompt import remember_user_app_version

        remember_user_app_version(user_id, getattr(websocket, "_trandsai_app_version", "") or "")
    except Exception:
        logger.debug("[WS] persist app_version skipped", exc_info=True)

    from services.companion_report import assert_companion_visible_to_user

    vis_err = assert_companion_visible_to_user(user_id, companion_id)
    if vis_err:
        logger.warning(
            "[WS-DEBUG]   ❌ companion 对当前用户不可见: %s | user=%s | reason=%s",
            companion_id, user_id, vis_err,
        )
        await websocket.close(code=1008, reason=vis_err)
        return

    companion = get_companion_manager().get(companion_id, include_deleted=True)
    if not companion:
        logger.warning("[WS-DEBUG]   ❌ companion 未找到: %s", companion_id)
        err = _WS_COMPANION_NOT_FOUND.get(ui_lang_early, _WS_COMPANION_NOT_FOUND["en"])
        await _ws_send_json(websocket, {"type": "error", "text": err})
        await websocket.close()
        return
    logger.info("[WS-DEBUG]   companion 找到: %s (deleted=%s)", companion_id, getattr(companion.profile, "is_deleted", None))

    cm = get_companion_manager()
    # 以 DB 为准同步软删标记（多 worker 缓存可能滞后）
    is_soft_deleted = bool(companion.profile.is_deleted) or cm.is_soft_deleted(companion_id)
    if is_soft_deleted:
        companion.profile.is_deleted = True

    try:
        logger.info("[WS-DEBUG]   检查 companion_access: user=%s companion=%s", user_id, companion_id)
        _assert_companion_user_access(companion, user_id)
        logger.info("[WS-DEBUG]   ✅ companion_access 通过")
    except HTTPException as e:
        logger.warning("[WS-DEBUG]   ❌ companion_access 拒绝: status=%s detail=%s", e.status_code, e.detail)
        try:
            detail = (
                e.detail
                if isinstance(e.detail, str) and e.detail.strip()
                else None
            )
            err = detail or _WS_ACCESS_DENIED.get(ui_lang_early, _WS_ACCESS_DENIED["en"])
            await _ws_send_json(websocket, {"type": "error", "text": err})
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    lang_param = normalize_ui_language(websocket.query_params.get("lang"))
    session_meta = get_session(companion_id, user_id=user_id)
    session_meta["lang"] = lang_param
    session_meta["user_id"] = user_id  # 记录用户ID用于后续会话
    session_meta["app_version"] = getattr(websocket, "_trandsai_app_version", "") or ""
    session_meta.pop("pending_retention", None)
    session_meta.pop("last_disconnect", None)
    await set_session(companion_id, session_meta, user_id=user_id)
    _ws_bind_memory(websocket, companion, user_id)
    user_lang = session_meta.get("lang") or "en"
    connection_id = str(uuid.uuid4())
    _ws_register(connection_id, websocket)
    _ws_bind_session(websocket, user_id, companion_id)
    await _ensure_ws_janitor()

    try:
        from services.user_first_chat import claim_first_chat, first_chat_hello_payload

        _first_chat = claim_first_chat(int(user_id), companion_id)
        logger.info(
            "[WS] first_chat user=%s companion=%s first_chat=%s",
            user_id,
            companion_id,
            _first_chat,
        )
        await _ws_send_json(
            websocket, first_chat_hello_payload(int(user_id), companion_id, _first_chat)
        )
    except Exception:
        logger.exception(
            "[WS] first_chat hello failed user=%s companion=%s",
            user_id,
            companion_id,
        )

    # 进房立刻推免费次数（先于未读清理 / 历史计数，避免 UI 等握手）
    try:
        from services.credit_service import (
            free_turn_used_payload,
            get_companion_free_turns_snapshot,
            is_vip_active,
        )
        from services.say_hi_quota import get_say_hi_daily_snapshot
        from core.database import UserCreditORM

        with get_db() as db:
            _uc = (
                db.query(UserCreditORM)
                .filter(UserCreditORM.user_id == int(user_id))
                .first()
            )
            _is_vip = bool(is_vip_active(_uc)) if _uc else False
            _free_snap = get_companion_free_turns_snapshot(db, user_id, companion_id)
        if not _is_vip:
            await _ws_send_json(
                websocket,
                free_turn_used_payload(
                    _free_snap,
                    report_free_quota_reached=False,
                    say_hi_snap=get_say_hi_daily_snapshot(user_id),
                ),
            )
    except Exception:
        logger.exception(
            "[WS] push free turns on connect failed user=%s companion=%s",
            user_id,
            companion_id,
        )

    try:
        from services.user_chat_unread import clear_user_chat_unread

        clear_user_chat_unread(int(user_id), companion_id, notify=True)
    except Exception:
        logger.debug(
            "clear user unread on chat connect skipped user=%s companion=%s",
            user_id,
            companion_id,
            exc_info=True,
        )

    # 软删：连接保持静默（可看历史）；提示延后到用户真正发消息时
    deleted_tip_sent = False

    # 检查是否有未展示的朋友圈详情（moment）
    from core.database import ShortTermMessageORM
    with get_db() as db:
        message_count = db.query(ShortTermMessageORM).filter(
            ShortTermMessageORM.companion_id == companion_id,
            ShortTermMessageORM.user_id == user_id
        ).count()

        if message_count < 2:
            first_message = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == companion_id,
                ShortTermMessageORM.user_id == user_id
            ).order_by(ShortTermMessageORM.created_at.asc()).first()

            if first_message and first_message.role == "moment":
                # moment_data = json.loads(first_message.content)
                # await _ws_send_json(websocket, {
                #     "type": "moment",
                #     "data": moment_data
                # })
                # 标记为已展示
                first_message.role = "moment"

    # 消息去重：记录最近收到的消息内容和时间戳
    _recent_user_messages: list[dict] = []

    # ===== DEBUG: 连接完全建立 =====
    logger.info(
        "[WS-DEBUG]   🟢 连接完全建立 | user=%s | companion=%s | lang=%s | setup耗时=%.1fms | 进入主消息循环",
        user_id, companion_id, lang_param, (time.monotonic() - _dbg_ts) * 1000,
    )

    # 不在每次 WS 握手后推送「已连接」类系统提示，避免离线重连打扰用户（静默连接）

    proactive_task = None

    # 用户消息队列：解耦接收与处理，支持发送过程中被打断
    message_queue: asyncio.Queue = asyncio.Queue()
    turn_in_progress = False

    async def _receive_loop():
        """持续接收用户消息，存入队列；应答/刷新应用层心跳。"""
        while True:
            try:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"text": raw}
                msg_type = payload.get("type")
                if msg_type == "ping":
                    # 客户端心跳：回 pong，并记存活
                    _ws_mark_alive(websocket)
                    await _ws_send_json(websocket, {"type": "pong"})
                    continue
                if msg_type == "pong":
                    # 应答服务端 ping：刷新存活，解除 awaiting
                    _ws_mark_alive(websocket)
                    continue
                user_text = _ws_payload_user_text(payload)
                if user_text:
                    _ws_mark_alive(websocket)
                    await message_queue.put(payload)
                    if turn_in_progress:
                        try:
                            await _ws_send_json(websocket, {"type": "queued"})
                        except Exception:
                            pass
            except WebSocketDisconnect:
                # 立刻清会话在线，避免 AI 仍在生成时未读被跳过
                _ws_clear_session_online(websocket)
                await message_queue.put({"__disconnect": True})
                break
            except Exception:
                _ws_clear_session_online(websocket)
                await message_queue.put({"__disconnect": True})
                break

    receiver_task = asyncio.create_task(_receive_loop())

    try:
        while True:
            turn_in_progress = False
            # 空闲等待用户消息；超时则发 ping，撑住代理读超时；无 pong 则清僵死连接
            try:
                payload = await asyncio.wait_for(
                    message_queue.get(), timeout=_ws_heartbeat_interval()
                )
            except asyncio.TimeoutError:
                if not await _ws_send_ping_or_close(websocket):
                    break
                continue
            if payload.get("__disconnect"):
                _ws_clear_session_online(websocket)
                if proactive_task and not proactive_task.done():
                    proactive_task.cancel()
                prev_sess = get_session(companion_id, user_id=user_id)
                prev_lang = prev_sess.get("lang")
                next_sess = {**prev_sess, "lang": prev_lang or user_lang or "en", "user_id": user_id}
                next_sess.pop("pending_retention", None)
                next_sess.pop("last_disconnect", None)
                await set_session(companion_id, next_sess, user_id=user_id)
                break
            first_text = _ws_payload_user_text(payload)
            if not first_text:
                continue
            turn_in_progress = True

            # 软删后禁止对话：发消息时提示（type=error 便于前端展示）；本连接只提示一次
            if is_soft_deleted or cm.is_soft_deleted(companion_id):
                is_soft_deleted = True
                companion.profile.is_deleted = True
                if not deleted_tip_sent:
                    tip = _WS_COMPANION_SOFT_DELETED.get(
                        normalize_ui_language(payload.get("lang") or user_lang or "en"),
                        _WS_COMPANION_SOFT_DELETED["en"],
                    )
                    await _ws_send_json(websocket, {
                        "type": "error",
                        "text": tip,
                        "is_deleted": True,
                        "code": "companion_deleted",
                    })
                    deleted_tip_sent = True
                # 吞掉用户消息，不入记忆、不调模型
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
                newer_text = _ws_payload_user_text(newer)
                if newer_text:
                    burst_parts.append(newer_text)
                    coalesce_skipped += 1

            combined_user_plain = "\n".join(burst_parts)
            lk_co = normalize_ui_language(burst_head.get("lang") or user_lang or "en")

            if coalesce_skipped >= 2:
                co_txt = _QUEUE_COALESCED_MESSAGE.get(lk_co, _QUEUE_COALESCED_MESSAGE["en"])
                await _ws_send_json(websocket, {"type": "system", "text": co_txt})

            # 消息去重 / 连点防抖：短窗内相同正文静默跳过（不回「有点忙」、不扣新一轮）
            now = datetime.now(timezone.utc)
            _plain_stripped = combined_user_plain.strip()
            is_duplicate = False
            debounce_s = float(os.getenv("CHAT_DEBOUNCE_S", str(_CHAT_DEBOUNCE_S)) or _CHAT_DEBOUNCE_S)
            if _plain_stripped:
                for m in _recent_user_messages:
                    if m.get("text") != combined_user_plain:
                        continue
                    age = (now - m["time"]).total_seconds()
                    # 长句：原 5s；短句/任意：debounce 窗
                    if age < debounce_s:
                        is_duplicate = True
                        break
                    if (
                        len(_plain_stripped) >= _DUPLICATE_CHECK_MIN_LEN
                        and age < 5
                    ):
                        is_duplicate = True
                        break
            if is_duplicate:
                logger.info(
                    "chat debounce skip user=%s companion=%s chars=%s",
                    user_id,
                    companion_id,
                    len(_plain_stripped),
                )
                continue
            _recent_user_messages.append({"text": combined_user_plain, "time": now})
            if len(_recent_user_messages) > 10:
                _recent_user_messages.pop(0)

            # 用户发新消息了，取消之前的主动消息定时任务
            if proactive_task and not proactive_task.done():
                proactive_task.cancel()

            if not check_chat_rate_limit(user_id):
                # 限流：不向会话塞「有点忙」伪角色回复；仅记日志并跳过本轮
                logger.info("chat rate limited user=%s (no busy text to user)", user_id)
                continue

            # ===== 积分/免费轮次（接管期间与 AI 对话相同规则扣费）=====
            from services.chat_turn import ChatTurnError, charge_chat_turn, refund_chat_turn

            t_prep0 = _time_mod.monotonic()
            try:
                _charge_meta = charge_chat_turn(user_id, companion_id)
            except ChatTurnError as e:
                stored = _ws_user_memory(websocket, companion).persist_unpaid_user_messages(
                    burst_parts
                )
                from services.chat_turn import need_vip_ws_payload

                await _ws_send_json(websocket, need_vip_ws_payload(e, stored))
                try:
                    from services.im_admin import (
                        broadcast_admin_im,
                        is_session_taken_over,
                        notify_ops_new_user_message,
                    )

                    await notify_ops_new_user_message(
                        user_id,
                        companion_id,
                        combined_user_plain,
                        delta=1,
                    )
                    if is_session_taken_over(user_id, companion_id):
                        await broadcast_admin_im(
                            user_id,
                            companion_id,
                            {
                                "type": "message",
                                "role": "user",
                                "text": combined_user_plain,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                                "send_status": "unpaid",
                            },
                        )
                except Exception:
                    logger.exception(
                        "unpaid chat IM notify failed user=%s companion=%s",
                        user_id,
                        companion_id,
                    )
                continue
            t_charge_ms = int((_time_mod.monotonic() - t_prep0) * 1000)

            if _charge_meta.get("use_free"):
                from services.credit_service import free_turn_used_payload
                from services.say_hi_quota import get_say_hi_daily_snapshot

                await _ws_send_json(
                    websocket,
                    free_turn_used_payload(
                        {
                            "free_turns_remaining": _charge_meta.get("free_turns_remaining") or 0,
                            "free_turns_limit": _charge_meta.get("free_turns_limit") or 0,
                            "free_turns_used": _charge_meta.get("free_turns_used") or 0,
                        },
                        report_free_quota_reached=bool(
                            _charge_meta.get("report_free_quota_reached")
                        ),
                        say_hi_snap=get_say_hi_daily_snapshot(user_id),
                    ),
                )

            album_event = None
            for line in burst_parts:
                ev = _ws_user_memory(websocket, companion).add_user_message(line)
                if isinstance(ev, dict):
                    if album_event is None:
                        album_event = dict(ev)
                        album_event["just_unlocked"] = list(ev.get("just_unlocked") or [])
                    else:
                        album_event.update(
                            {
                                k: ev.get(k)
                                for k in (
                                    "remaining_messages",
                                    "unlock_x",
                                    "all_unlocked",
                                    "unlocked_count",
                                    "photo_count",
                                )
                            }
                        )
                        # 本轮已解锁时，后续消息不要把 toast 盖成空
                        if ev.get("toast"):
                            album_event["toast"] = ev.get("toast")
                        album_event["just_unlocked"] = list(
                            album_event.get("just_unlocked") or []
                        ) + list(ev.get("just_unlocked") or [])
            if album_event and album_event.get("toast"):
                await _ws_send_json(
                    websocket,
                    {"type": "our_moments", **album_event},
                )

            # ===== 快速回复：WS 收到用户原文后立刻匹配，命中则回一条并跳过 AI =====
            try:
                from services.quick_reply import try_deliver_quick_reply
                from services.prompts.resolve import resolve_lang as _resolve_lang_qr

                qr_lang = _resolve_lang_qr(
                    body_lang=burst_head.get("lang"),
                    query_lang=None,
                    session_lang=get_session(companion_id, user_id=user_id).get("lang")
                    or user_lang
                    or "en",
                )
                logger.info(
                    "[quick_reply] try user=%s companion=%s lang=%s text=%s",
                    user_id,
                    companion_id,
                    qr_lang,
                    (combined_user_plain or "")[:80],
                )
                if await try_deliver_quick_reply(
                    websocket=websocket,
                    companion=companion,
                    user_id=user_id,
                    companion_id=companion_id,
                    user_text=combined_user_plain,
                    language=qr_lang,
                    send_json=_ws_send_json,
                ):
                    try:
                        from services.im_admin import notify_ops_new_user_message

                        await notify_ops_new_user_message(
                            user_id,
                            companion_id,
                            combined_user_plain,
                            delta=1,
                        )
                    except Exception:
                        pass
                    continue
            except Exception:
                logger.exception(
                    "quick_reply branch failed user=%s companion=%s",
                    user_id,
                    companion_id,
                )

            # ===== IM：责任运营未读 + inbox 提醒（接管与否都通知）=====
            try:
                from services.im_admin import notify_ops_new_user_message

                await notify_ops_new_user_message(
                    user_id,
                    companion_id,
                    combined_user_plain,
                    delta=1,
                )
            except Exception:
                logger.exception(
                    "IM ops unread notify failed user=%s companion=%s",
                    user_id,
                    companion_id,
                )

            # ===== 人工接管：落库用户消息并推运营 WS，不走 AI =====
            try:
                from services.im_admin import broadcast_admin_im, is_session_taken_over

                if is_session_taken_over(user_id, companion_id):
                    try:
                        from core.database import UserCompanionStateORM, get_db as _get_db

                        with _get_db() as _db:
                            _ucs = (
                                _db.query(UserCompanionStateORM)
                                .filter(
                                    UserCompanionStateORM.user_id == user_id,
                                    UserCompanionStateORM.companion_id == companion_id,
                                )
                                .first()
                            )
                            if _ucs:
                                _ucs.updated_at = datetime.now(timezone.utc)
                    except Exception:
                        pass
                    await broadcast_admin_im(
                        user_id,
                        companion_id,
                        {
                            "type": "message",
                            "role": "user",
                            "text": combined_user_plain,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    continue
            except Exception:
                logger.exception(
                    "IM takeover branch failed user=%s companion=%s",
                    user_id,
                    companion_id,
                )

            # ===== 优先人工：等待窗口内暂缓 AI =====
            try:
                from services.im_admin import broadcast_admin_im
                from services.im_reply_mode import try_defer_human_first

                if try_defer_human_first(user_id, companion_id):
                    await broadcast_admin_im(
                        user_id,
                        companion_id,
                        {
                            "type": "message",
                            "role": "user",
                            "text": combined_user_plain,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "source": "human_first_wait",
                        },
                    )
                    continue
            except Exception:
                logger.exception(
                    "IM human_first defer failed user=%s companion=%s",
                    user_id,
                    companion_id,
                )

            # 如果有未发送完的AI回复，先保存已发送的部分（如果有）
            # 这里简化处理：已发送的内容已经展示给用户，新的回复会覆盖话题

            has_leave_intent = detect_leave_intent(combined_user_plain)

            if len(burst_parts) > 1:
                prefix = _MULTI_MESSAGE_AGENT_PREFIX.get(lk_co, _MULTI_MESSAGE_AGENT_PREFIX["en"])
                numbered = "\n".join(f"({i + 1}) {t}" for i, t in enumerate(burst_parts))
                user_text_for_agent = prefix + numbered
            else:
                user_text_for_agent = burst_parts[0]
            # typing 发失败（用户已离页）仍继续生成，回复会落库并可转投到新连接
            await _ws_send_json(websocket, {"type": "typing"})

            memory_tier = os.getenv("AGENT_MEMORY_TIER", "full")
            relation_card_text = ""
            card = {}
            open_threads: list = []
            deny_hooks: list = []
            t_mem0 = _time_mod.monotonic()
            # 先解析语种，关系卡标题跟 UI/会话语种走
            language = resolve_lang(
                body_lang=burst_head.get("lang"),
                query_lang=None,
                session_lang=get_session(companion_id, user_id=user_id).get("lang")
                or user_lang
                or "en",
            )
            if RELATION_CARD_ENABLED:
                card = load_card(user_id, companion_id)
                if not is_card_empty(card):
                    relation_card_text = render_card(card, lang=language)
                open_threads = list(card.get("open_threads") or [])
                deny_hooks = list(card.get("deny_hooks") or [])
            use_card = bool(relation_card_text)
            fallback_full = RELATION_CARD_FALLBACK_FULL_MEMORY and is_card_empty(card)
            memory_text = _ws_user_memory(websocket, companion).build_prompt_context(
                query=combined_user_plain,
                tier=memory_tier,
                user_id=user_id,
                relation_card_text=relation_card_text if use_card else "",
                truncate_episodes=use_card and not fallback_full,
                lang=language,
            )
            recent_assistant = []
            recent_user = []
            try:
                # 与近聊语料同窗：助手句供忌用；用户句供回复语种兜底
                from services.memory import MEMORY_RECENT_MESSAGES
                from services.user_facing_lang import recent_user_texts_from_messages

                recent_msgs = llm_visible_messages(
                    _ws_user_memory(websocket, companion).short_term.get_recent(
                        MEMORY_RECENT_MESSAGES
                    )
                )
                for m in recent_msgs:
                    if m.get("role") == "assistant" and (m.get("content") or "").strip():
                        recent_assistant.append(m["content"])
                # 勿复读对照窗略加长（与 RECENT_ASSISTANT_* 对齐量级）
                recent_assistant = recent_assistant[-16:]
                recent_user = recent_user_texts_from_messages(recent_msgs)
            except Exception:
                recent_assistant = []
                recent_user = []
            # 忌用窗以近聊为准补齐（关系卡 deny 常被异步事实任务冲空）
            deny_hooks = seed_deny_from_recent(deny_hooks, recent_assistant)
            t_memory_ms = int((_time_mod.monotonic() - t_mem0) * 1000)
            # C3 / M*：idle 与极端冷却时间戳（先算 idle，再刷新 last_user_ts）
            session_meta_pre = get_session(companion_id, user_id=user_id) or {}
            idle_seconds = None
            raw_prev_ts = session_meta_pre.get("last_user_ts")
            if raw_prev_ts:
                try:
                    prev_ts = datetime.fromisoformat(str(raw_prev_ts).replace("Z", "+00:00"))
                    if prev_ts.tzinfo is None:
                        prev_ts = prev_ts.replace(tzinfo=timezone.utc)
                    idle_seconds = (datetime.now(timezone.utc) - prev_ts).total_seconds()
                except Exception:
                    idle_seconds = None
            last_extreme_ts = str(session_meta_pre.get("last_extreme_ts") or "")
            reply_lang_override = str(session_meta_pre.get("reply_lang_override") or "")
            session_meta_pre["last_user_ts"] = datetime.now(timezone.utc).isoformat()
            await set_session(companion_id, session_meta_pre, user_id=user_id)
            session_meta = get_session(companion_id, user_id=user_id)
            session_meta["lang"] = language
            tz_pay = (burst_head.get("tz") or burst_head.get("timeZone") or "").strip()
            if tz_pay:
                session_meta["client_tz"] = tz_pay[:120]
            if burst_head.get("tz_offset") is not None:
                try:
                    session_meta["client_tz_offset"] = int(burst_head["tz_offset"])
                except (TypeError, ValueError):
                    pass
            await set_session(companion_id, session_meta, user_id=user_id)

            tz_iana_effective = (
                (burst_head.get("tz") or burst_head.get("timeZone") or "").strip()
                or (session_meta.get("client_tz") or "").strip()
                or None
            )
            if tz_iana_effective:
                tz_iana_effective = tz_iana_effective[:120]
                try:
                    from services.outreach import remember_user_timezone

                    remember_user_timezone(user_id, tz_iana_effective)
                except Exception:
                    pass
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
            summary_due = _ws_user_memory(websocket, companion).summary.should_update()

            result = None
            # 当前环境无可靠 agent-worker：强制直跑。arq 入队会干等超时，表现为「不回/超时」。
            # 需要队列时设 AGENT_QUEUE=1 并启动 agent-worker 后再打开。
            use_queue = False
            t_turn0 = _time_mod.monotonic()
            turn_error = None
            light_path = False
            was_interrupted = False
            pronoun = _companion_pronoun(companion)
            burst_count = len(burst_parts)
            t_lock_wait_ms = 0
            t_agent_ms = 0
            t_deliver_ms = 0
            t_prep_ms = int((_time_mod.monotonic() - t_prep0) * 1000)

            _turn_lock, _lock_ok, t_lock_wait_ms = await _acquire_turn_lock_for_ws(
                websocket, user_id, companion_id
            )
            # 等不及也不对用户回「有点忙」（易被做成角色气泡）；退款并记日志即可
            if not _lock_ok:
                refund_chat_turn(user_id, _charge_meta)
                _log_chat_turn({
                    "companion_id": companion_id,
                    "user_id": user_id,
                    "light_path": False,
                    "error": "turn_lock_busy",
                    "t_charge_ms": t_charge_ms,
                    "t_memory_ms": t_memory_ms,
                    "t_prep_ms": t_prep_ms,
                    "t_lock_wait_ms": t_lock_wait_ms,
                    "t_first_message_ms": int((_time_mod.monotonic() - t_turn0) * 1000),
                })
                continue

            try:
                if use_queue:
                    job = AgentJob(
                        job_id=new_job_id(),
                        connection_id=connection_id,
                        companion_id=companion_id,
                        user_id=user_id,
                        user_text_for_agent=user_text_for_agent,
                        combined_user_plain=combined_user_plain,
                        profile=companion.profile.model_dump(),
                        companion_state=companion.state.model_dump(),
                        memory_text=memory_text,
                        language=language,
                        current_time=current_time,
                        user_gender=user_gender,
                        summary_due=summary_due,
                        has_leave_intent=has_leave_intent,
                        burst_count=burst_count,
                        priority=int(os.getenv("PAID_USER_PRIORITY", "0")),
                        open_threads=open_threads,
                        deny_hooks=deny_hooks,
                        recent_assistant=recent_assistant,
                        recent_user=recent_user,
                        idle_seconds=float(idle_seconds) if idle_seconds is not None else -1.0,
                        last_extreme_ts=last_extreme_ts,
                        reply_lang_override=reply_lang_override,
                        source="ws",
                        app_version=getattr(websocket, "_trandsai_app_version", "") or "",
                    )
                    enqueued = await enqueue_agent_job(job)
                    if enqueued:
                        pending = loop.create_future()
                        keep_task = asyncio.create_task(
                            _keepalive_during_agent(websocket, pending, language)
                        )
                        try:
                            payload = await wait_agent_result(connection_id, job.job_id)
                            if payload and payload.get("ok"):
                                result = payload.get("result")
                            else:
                                logger.warning(
                                    "Agent queue job %s failed or timed out (payload=%s), fallback to direct",
                                    job.job_id,
                                    bool(payload),
                                )
                                use_queue = False
                        finally:
                            if not pending.done():
                                pending.set_result(True)
                            keep_task.cancel()
                            try:
                                await keep_task
                            except asyncio.CancelledError:
                                pass
                    else:
                        use_queue = False

                if not use_queue and result is None:
                    # 预判轻路径：轻路径不占重路径信号量（REQ-A6）
                    from services.agent import should_use_light_path as _sulp
                    will_light = _sulp(
                        user_text_for_agent,
                        companion.state.model_dump(),
                        has_leave_intent=has_leave_intent,
                        burst_count=burst_count,
                    )

                    async def _run_agent_coro():
                        return await asyncio.wrap_future(
                            loop.run_in_executor(
                                AGENT_POOL,
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
                                    summary_due,
                                    True,
                                    has_leave_intent,
                                    burst_count,
                                    open_threads,
                                    deny_hooks,
                                    recent_assistant,
                                    recent_user,
                                    idle_seconds,
                                    last_extreme_ts,
                                    reply_lang_override,
                                    user_id,
                                    getattr(websocket, "_trandsai_app_version", "") or "",
                                ),
                            )
                        )

                    acquired = False
                    if not will_light:
                        slot_wait_s = float(os.getenv("HEAVY_SLOT_WAIT_S", "120") or 120)

                        async def _sem_acquire() -> bool:
                            try:
                                await asyncio.wait_for(
                                    agent_semaphore.acquire(), timeout=slot_wait_s
                                )
                                return True
                            except asyncio.TimeoutError:
                                return False

                        sem_task = asyncio.create_task(_sem_acquire())
                        try:
                            acquired = await _wait_acquire_with_typing(
                                websocket, sem_task, label="heavy_slot"
                            )
                        except Exception:
                            if not sem_task.done():
                                sem_task.cancel()
                                try:
                                    await sem_task
                                except Exception:
                                    pass
                            acquired = False
                        if not acquired:
                            # 不对用户发「有点忙」角色/系统气泡；退款并记日志
                            logger.info(
                                "heavy slot wait exhausted user=%s companion=%s (no busy text)",
                                user_id,
                                companion_id,
                            )
                            _log_chat_turn({
                                "companion_id": companion_id,
                                "user_id": user_id,
                                "light_path": False,
                                "need_facts": False,
                                "need_summary": summary_due,
                                "evolved": False,
                                "llm_calls_est": 0,
                                "t_first_message_ms": int((_time_mod.monotonic() - t_turn0) * 1000),
                                "interrupted": False,
                                "error": "busy",
                            })
                            refund_chat_turn(user_id, _charge_meta)
                            continue

                    agent_task = asyncio.create_task(_run_agent_coro())
                    keep_task = asyncio.create_task(
                        _keepalive_during_agent(websocket, agent_task, language)
                    )
                    t_agent0 = _time_mod.monotonic()
                    try:
                        result = await asyncio.wait_for(asyncio.shield(agent_task), timeout=120.0)
                        t_agent_ms = int((_time_mod.monotonic() - t_agent0) * 1000)
                    except asyncio.TimeoutError:
                        t_agent_ms = int((_time_mod.monotonic() - t_agent0) * 1000)
                        turn_error = "timeout"
                        agent_task.cancel()
                        err_txt = _AGENT_TIMEOUT_MESSAGE.get(language, _AGENT_TIMEOUT_MESSAGE["en"])
                        await _ws_send_json(websocket, {"type": "error", "text": err_txt})
                        _log_chat_turn({
                            "companion_id": companion_id,
                            "user_id": user_id,
                            "light_path": will_light,
                            "need_facts": False,
                            "need_summary": summary_due,
                            "evolved": False,
                            "llm_calls_est": 0,
                            "t_charge_ms": t_charge_ms,
                            "t_memory_ms": t_memory_ms,
                            "t_prep_ms": t_prep_ms,
                            "t_lock_wait_ms": t_lock_wait_ms,
                            "t_agent_ms": t_agent_ms,
                            "t_first_message_ms": int((_time_mod.monotonic() - t_turn0) * 1000),
                            "interrupted": False,
                            "error": turn_error,
                        })
                        refund_chat_turn(user_id, _charge_meta)
                        continue
                    except Exception as agent_exc:
                        t_agent_ms = int((_time_mod.monotonic() - t_agent0) * 1000)
                        turn_error = "agent_error"
                        logger.exception(
                            "Agent direct run failed companion=%s user=%s: %s",
                            companion_id,
                            user_id,
                            agent_exc,
                        )
                        err_txt = _AGENT_TIMEOUT_MESSAGE.get(language, _AGENT_TIMEOUT_MESSAGE["en"])
                        await _ws_send_json(
                            websocket,
                            {"type": "error", "text": err_txt},
                        )
                        _log_chat_turn({
                            "companion_id": companion_id,
                            "user_id": user_id,
                            "light_path": will_light,
                            "need_facts": False,
                            "need_summary": summary_due,
                            "evolved": False,
                            "llm_calls_est": 0,
                            "t_charge_ms": t_charge_ms,
                            "t_memory_ms": t_memory_ms,
                            "t_prep_ms": t_prep_ms,
                            "t_lock_wait_ms": t_lock_wait_ms,
                            "t_agent_ms": t_agent_ms,
                            "t_first_message_ms": int((_time_mod.monotonic() - t_turn0) * 1000),
                            "interrupted": False,
                            "error": f"{type(agent_exc).__name__}: {agent_exc}",
                        })
                        refund_chat_turn(user_id, _charge_meta)
                        continue
                    finally:
                        if acquired:
                            agent_semaphore.release()
                        keep_task.cancel()
                        try:
                            await keep_task
                        except asyncio.CancelledError:
                            pass

                if not result:
                    refund_chat_turn(user_id, _charge_meta)
                    continue

                light_path = bool(result.get("light_path"))
                response_text = result["response"]
                # 清理 JSON 格式的思考过程（思考/反思/创意），这些是内部推理不应展示给用户
                response_text = _strip_json_think_blocks(response_text)

                # REQ-A7：无首条前拟人等待
                aff = float(getattr(companion.state, "affection", 0) or 0)
                show_think = random.random() < _THINK_TOAST_UI_PROBABILITY * (
                    0.35 + 0.65 * min(1.0, aff / 100.0)
                )
                t_del0 = _time_mod.monotonic()
                sent_segments, was_interrupted = await _deliver_assistant_content(
                    websocket,
                    companion,
                    response_text,
                    message_queue=message_queue,
                    show_think_toasts=show_think,
                    delivery_mood=delivery_mood,
                )
                t_deliver_ms = int((_time_mod.monotonic() - t_del0) * 1000)
                t_first_ms = int((_time_mod.monotonic() - t_turn0) * 1000)
                timing = result.get("timing") or {}
                stages_ms = timing.get("stages_ms") or {}
                llm_calls = timing.get("llm_calls") or []
                llm_wait_ms = int(sum(float(c.get("wait_ms") or 0) for c in llm_calls))
                llm_call_ms = int(sum(float(c.get("call_ms") or 0) for c in llm_calls))
                logger.info(
                    "[chat.latency] turn user=%s companion=%s light=%s "
                    "prep=%sms mem=%sms lock=%sms agent=%sms deliver=%sms first=%sms "
                    "llm_wait=%sms llm_call=%sms stages=%s",
                    user_id,
                    companion_id,
                    light_path,
                    t_prep_ms,
                    t_memory_ms,
                    t_lock_wait_ms,
                    t_agent_ms,
                    t_deliver_ms,
                    t_first_ms,
                    llm_wait_ms,
                    llm_call_ms,
                    stages_ms,
                )

                memory_snapshot = result.get("memory_snapshot")

                if was_interrupted:
                    if sent_segments:
                        companion.state.mood = result["mood"]
                        companion.state.affection = result["affection"]
                        companion.state.turns += 1
                        # REQ-B5：打断已发气泡写入情景
                        _ws_user_memory(websocket, companion).commit_turn(
                            combined_user_plain,
                            "\n\n".join(sent_segments),
                            pronoun=pronoun,
                            partial=True,
                        )
                        if result.get("evolved_personality"):
                            companion.state.evolved_personality = result["evolved_personality"]
                        if result.get("evolved_background"):
                            companion.state.evolved_background = result["evolved_background"]
                        if result.get("evolved_speech_style"):
                            companion.state.evolved_speech_style = result["evolved_speech_style"]
                        if not memory_snapshot:
                            if _ws_user_memory(websocket, companion).summary.should_update() and result.get("new_summary"):
                                _ws_user_memory(websocket, companion).summary.update(result["new_summary"])
                                companion.state.summary = result["new_summary"]
                            else:
                                companion.state.summary = _ws_user_memory(websocket, companion).summary.get_summary()
                        companion.save_state(user_id=user_id)
                        if RELATION_CARD_ENABLED:
                            card2 = touch_state(
                                load_card(user_id, companion_id),
                                mood=companion.state.mood,
                                affection=companion.state.affection,
                                summary=companion.state.summary,
                            )
                            save_card(user_id, companion_id, card2)
                    if "reply_lang_override" in (result or {}):
                        from services.user_facing_lang import apply_sticky_to_session

                        sess_lang = get_session(companion_id, user_id=user_id) or {}
                        sess_lang = apply_sticky_to_session(
                            sess_lang, result.get("reply_lang_override")
                        )
                        await set_session(companion_id, sess_lang, user_id=user_id)
                    _log_chat_turn({
                        "companion_id": companion_id,
                        "user_id": user_id,
                        "light_path": light_path,
                        "need_facts": bool(result.get("need_facts")),
                        "need_summary": bool(result.get("need_summary", summary_due)),
                        "evolved": bool(result.get("evolved")),
                        "llm_calls_est": int(result.get("llm_calls_est") or 0),
                        "t_charge_ms": t_charge_ms,
                        "t_memory_ms": t_memory_ms,
                        "t_prep_ms": t_prep_ms,
                        "t_lock_wait_ms": t_lock_wait_ms,
                        "t_agent_ms": t_agent_ms,
                        "t_deliver_ms": t_deliver_ms,
                        "t_first_message_ms": t_first_ms,
                        "llm_wait_ms": llm_wait_ms,
                        "llm_call_ms": llm_call_ms,
                        "stages_ms": stages_ms,
                        "llm_calls": [
                            {
                                "node": c.get("node"),
                                "wait_ms": c.get("wait_ms"),
                                "call_ms": c.get("call_ms"),
                                "model": c.get("model"),
                                "in_chars": c.get("in_chars"),
                                "out_chars": c.get("out_chars"),
                            }
                            for c in llm_calls
                        ],
                        "prompt_system_chars": timing.get("prompt_system_chars"),
                        "prompt_memory_chars": timing.get("prompt_memory_chars"),
                        "prompt_turn_chars": timing.get("prompt_turn_chars"),
                        "interrupted": True,
                        "error": None,
                    })
                    continue

                actual_response = "\n\n".join(sent_segments)
                _ws_user_memory(websocket, companion).commit_turn(
                    combined_user_plain,
                    actual_response,
                    pronoun=pronoun,
                    partial=False,
                )

                companion.state.mood = result["mood"]
                _old_aff = float(getattr(companion.state, "affection", 0) or 0)
                companion.state.affection = result["affection"]
                companion.state.turns += 1

                try:
                    from services.outreach import schedule_turn_idle_recall

                    schedule_turn_idle_recall(
                        user_id=user_id,
                        companion_id=companion_id,
                        turns=int(companion.state.turns or 0),
                        companion_name=getattr(companion.profile, "name", "") or "",
                        tz_name=(session_meta.get("client_tz") if isinstance(session_meta, dict) else None),
                    )
                except Exception as _oe:
                    logger.warning("[chat] turn idle recall schedule failed: %s", _oe)

                try:
                    from services.outreach_phase3 import (
                        maybe_schedule_intimacy_event,
                        maybe_schedule_memory_milestone,
                    )

                    _cname = getattr(companion.profile, "name", "") or ""
                    maybe_schedule_intimacy_event(
                        user_id=user_id,
                        companion_id=companion_id,
                        old_affection=_old_aff,
                        new_affection=float(companion.state.affection or 0),
                        companion_name=_cname,
                    )
                    maybe_schedule_memory_milestone(
                        user_id=user_id,
                        companion_id=companion_id,
                        turns=int(companion.state.turns or 0),
                        companion_name=_cname,
                    )
                except Exception as _e3:
                    logger.warning("[chat] phase3 event schedule failed: %s", _e3)

                # 关系卡忌用指纹必须在后台任务前落库，避免 BG merge_facts 竞态清空
                if RELATION_CARD_ENABLED:
                    c = touch_state(
                        load_card(user_id, companion_id),
                        mood=companion.state.mood,
                        affection=companion.state.affection,
                        summary=companion.state.summary,
                    )
                    if result.get("new_facts") and not result.get("facts_async_pending"):
                        c = merge_facts(c, result.get("new_facts") or [], lang=language)
                    picked = (result.get("picked_hook") or "").strip()
                    if picked:
                        c = push_deny_hook(c, picked)
                        c = merge_open_threads(c, new_thread=picked)
                    c = push_deny_fingerprints(c, actual_response or response_text)
                    from services.affection_floor import effective_prompt_affection

                    stage = (result.get("relation_stage") or "").strip() or resolve_relation_stage(
                        int(getattr(companion.state, "turns", 0) or 0),
                        effective_prompt_affection(
                            float(getattr(companion.state, "affection", 0) or 0)
                        ),
                    )
                    c["stage"] = stage
                    save_card(user_id, companion_id, c)

                if memory_snapshot:
                    companion.save_state(user_id=user_id)
                    snapshot_for_bg = {
                        **memory_snapshot,
                        "companion_state": {
                            **memory_snapshot["companion_state"],
                            "turns": companion.state.turns,
                            "mood": companion.state.mood,
                            "affection": companion.state.affection,
                        },
                    }

                    async def _bg_memory(snap=snapshot_for_bg):
                        try:
                            mem = await run_memory_update_async(snap)
                            if mem.get("new_facts"):
                                _ws_user_memory(websocket, companion).facts.add_facts(mem["new_facts"])
                                if RELATION_CARD_ENABLED:
                                    c = load_card(user_id, companion_id)
                                    deny_before = list(c.get("deny_hooks") or [])
                                    stage_before = c.get("stage")
                                    c = merge_facts(c, mem["new_facts"], lang=language)
                                    # 后台不得冲掉主线程刚写入的忌用指纹/阶段
                                    latest = load_card(user_id, companion_id)
                                    merged = []
                                    seen = set()
                                    for d in (
                                        list(latest.get("deny_hooks") or [])
                                        + deny_before
                                        + list(c.get("deny_hooks") or [])
                                    ):
                                        s = str(d).strip()
                                        if s and s not in seen:
                                            seen.add(s)
                                            merged.append(s)
                                    c["deny_hooks"] = merged[-20:]
                                    c["stage"] = (
                                        latest.get("stage")
                                        or stage_before
                                        or c.get("stage")
                                        or "stranger"
                                    )
                                    save_card(user_id, companion_id, c)
                            if mem.get("new_summary") and _ws_user_memory(websocket, companion).summary.should_update():
                                _ws_user_memory(websocket, companion).summary.update(mem["new_summary"])
                                companion.state.summary = mem["new_summary"]
                            else:
                                companion.state.summary = _ws_user_memory(websocket, companion).summary.get_summary()
                            if mem.get("evolved_personality"):
                                companion.state.evolved_personality = mem["evolved_personality"]
                            if mem.get("evolved_background"):
                                companion.state.evolved_background = mem["evolved_background"]
                            if mem.get("evolved_speech_style"):
                                companion.state.evolved_speech_style = mem["evolved_speech_style"]
                            companion.save_state(user_id=user_id)
                        except Exception as e:
                            logger.warning("Background memory v2 failed: %s", e)

                    asyncio.create_task(_bg_memory())
                else:
                    if result.get("evolved_personality"):
                        companion.state.evolved_personality = result["evolved_personality"]
                    if result.get("evolved_background"):
                        companion.state.evolved_background = result["evolved_background"]
                    if result.get("evolved_speech_style"):
                        companion.state.evolved_speech_style = result["evolved_speech_style"]

                    if result.get("new_facts") and not result.get("facts_async_pending"):
                        _ws_user_memory(websocket, companion).facts.add_facts(result["new_facts"])

                    if _ws_user_memory(websocket, companion).summary.should_update() and result.get("new_summary"):
                        _ws_user_memory(websocket, companion).summary.update(result["new_summary"])
                        companion.state.summary = result["new_summary"]
                    else:
                        companion.state.summary = _ws_user_memory(websocket, companion).summary.get_summary()

                    companion.save_state(user_id=user_id)

                    # REQ-B7 / C4：异步 Facts（完整轮、非打断）；优先进档案队列
                    if result.get("facts_async_pending"):
                        timeout_s = float(os.getenv("FACTS_ASYNC_TIMEOUT_S", "45"))
                        job_payload = {
                            "kind": "facts",
                            "user_input": user_text_for_agent,
                            "final_response": actual_response,
                            "language": language,
                            "user_id": user_id,
                            "companion_id": companion_id,
                        }

                        async def _async_facts(payload=job_payload):
                            try:
                                inline = os.getenv("ARCHIVE_INLINE", "true").lower() in (
                                    "1",
                                    "true",
                                    "yes",
                                )
                                if archive_queue_enabled() and not inline:
                                    enqueue_archive_job(payload)
                                    return
                                mem = await asyncio.wait_for(
                                    loop.run_in_executor(
                                        AGENT_POOL, process_archive_job, payload
                                    ),
                                    timeout=timeout_s,
                                )
                                facts = (mem or {}).get("new_facts") or []
                                if facts:
                                    _ws_user_memory(websocket, companion).facts.add_facts(facts)
                                    if RELATION_CARD_ENABLED:
                                        c = load_card(user_id, companion_id)
                                        deny_keep = list(c.get("deny_hooks") or [])
                                        stage_keep = c.get("stage")
                                        c = merge_facts(
                                            touch_state(
                                                c,
                                                mood=companion.state.mood,
                                                affection=companion.state.affection,
                                                summary=companion.state.summary,
                                            ),
                                            facts,
                                            lang=language,
                                        )
                                        latest = load_card(user_id, companion_id)
                                        merged_deny = []
                                        seen_d = set()
                                        for d in (
                                            list(latest.get("deny_hooks") or [])
                                            + deny_keep
                                            + list(c.get("deny_hooks") or [])
                                        ):
                                            s = str(d).strip()
                                            if s and s not in seen_d:
                                                seen_d.add(s)
                                                merged_deny.append(s)
                                        c["deny_hooks"] = merged_deny[-20:]
                                        c["stage"] = (
                                            latest.get("stage")
                                            or stage_keep
                                            or c.get("stage")
                                            or "ambiguous"
                                        )
                                        save_card(user_id, companion_id, c)
                            except Exception as e:
                                logger.warning("Async facts failed/dropped: %s", e)

                        asyncio.create_task(_async_facts())

                if result.get("means_mode") == "extreme":
                    sess_x = get_session(companion_id, user_id=user_id) or {}
                    sess_x["last_extreme_ts"] = datetime.now(timezone.utc).isoformat()
                    await set_session(companion_id, sess_x, user_id=user_id)

                if "reply_lang_override" in (result or {}):
                    from services.user_facing_lang import apply_sticky_to_session

                    sess_lang = get_session(companion_id, user_id=user_id) or {}
                    sess_lang = apply_sticky_to_session(
                        sess_lang, result.get("reply_lang_override")
                    )
                    await set_session(companion_id, sess_lang, user_id=user_id)

                _log_chat_turn({
                    "companion_id": companion_id,
                    "user_id": user_id,
                    "light_path": light_path,
                    "need_facts": bool(result.get("need_facts")),
                    "need_summary": bool(result.get("need_summary", summary_due)),
                    "evolved": bool(result.get("evolved")),
                    "llm_calls_est": int(result.get("llm_calls_est") or 0),
                    "t_charge_ms": t_charge_ms,
                    "t_memory_ms": t_memory_ms,
                    "t_prep_ms": t_prep_ms,
                    "t_lock_wait_ms": t_lock_wait_ms,
                    "t_agent_ms": t_agent_ms,
                    "t_deliver_ms": t_deliver_ms,
                    "t_first_message_ms": t_first_ms,
                    "llm_wait_ms": llm_wait_ms,
                    "llm_call_ms": llm_call_ms,
                    "stages_ms": stages_ms,
                    "llm_calls": [
                        {
                            "node": c.get("node"),
                            "wait_ms": c.get("wait_ms"),
                            "call_ms": c.get("call_ms"),
                            "model": c.get("model"),
                            "in_chars": c.get("in_chars"),
                            "out_chars": c.get("out_chars"),
                        }
                        for c in llm_calls
                    ],
                    "prompt_system_chars": timing.get("prompt_system_chars"),
                    "prompt_memory_chars": timing.get("prompt_memory_chars"),
                    "prompt_turn_chars": timing.get("prompt_turn_chars"),
                    "interrupted": False,
                    "error": None,
                    "threat_level": result.get("threat_level"),
                    "means_mode": result.get("means_mode"),
                    "extreme_reason": result.get("extreme_reason"),
                })
            finally:
                try:
                    _turn_lock.release()
                except Exception:
                    pass

            # AI 回复完成后，启动新的主动消息定时任务（连接已断则交给新 WS）
            if websocket.client_state == WebSocketState.CONNECTED:
                proactive_task = asyncio.create_task(
                    _send_proactive_message(websocket, companion, language, companion_id, user_id=user_id)
                )

    except WebSocketDisconnect as _ws_disc:
        # ===== DEBUG: 记录断开的 close code =====
        _disc_code = getattr(_ws_disc, "code", None)
        _disc_reason = getattr(_ws_disc, "reason", None)
        _conn_dur = time.monotonic() - _dbg_ts
        logger.info(
            "[WS-DEBUG] ◀ WebSocketDisconnect | companion=%s | user=%s | code=%s reason='%s' | 连接持续 %.1fs",
            companion_id, user_id, _disc_code, _disc_reason, _conn_dur,
        )
        if proactive_task and not proactive_task.done():
            proactive_task.cancel()
        prev_sess = get_session(companion_id, user_id=user_id)
        prev_lang = prev_sess.get("lang")
        next_sess = {**prev_sess, "lang": prev_lang or user_lang or "en", "user_id": user_id}
        next_sess.pop("pending_retention", None)
        next_sess.pop("last_disconnect", None)
        await set_session(companion_id, next_sess, user_id=user_id)
    except Exception as _ws_exc:
        _conn_dur = time.monotonic() - _dbg_ts
        logger.warning(
            "[WS-DEBUG] ◀ 异常退出 | companion=%s | user=%s | 连接持续 %.1fs | exc=%s: %s",
            companion_id, user_id, _conn_dur, type(_ws_exc).__name__, _ws_exc,
            exc_info=True,
        )
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                lk = normalize_ui_language(get_session(companion_id, user_id=user_id).get("lang") or "en")
                msg = _WS_CHAT_UNEXPECTED_ERROR.get(lk, _WS_CHAT_UNEXPECTED_ERROR["en"])
                await _ws_send_json(websocket, {"type": "error", "text": msg})
        except Exception as send_err:
            logger.warning(
                "Failed to send websocket error payload for companion %s: %s",
                companion_id,
                send_err,
            )
    finally:
        _ws_unregister(connection_id)
        _conn_dur = time.monotonic() - _dbg_ts
        logger.info(
            "[WS-DEBUG] ◀ finally | companion=%s | user=%s | 总持续 %.1fs | client_state=%s",
            companion_id, user_id, _conn_dur, getattr(websocket, "client_state", None),
        )
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
        # 确保僵死/异常路径也释放底层连接
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
