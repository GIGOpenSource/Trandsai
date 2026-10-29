import os
import re
import threading
import time
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings

from core.chroma_client import get_persistent_client
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from openai import OpenAI
from sqlalchemy import desc

from core.database import (
    FactORM,
    RelationSummaryORM,
    UserRelationSummaryORM,
    ShortTermMessageORM,
    get_db,
)
from core.chat_cache import (
    append_message as redis_append_message,
    bridge_enabled,
    clear as redis_clear_messages,
    get_last_assistant_content as redis_get_last_assistant,
    get_recent as redis_get_recent,
    get_room_cleared_at,
    get_total_count as redis_get_total_count,
    message_at_or_before_clear,
    purge_room_cache,
    warm_from_db as redis_warm_from_db,
)

logger = logging.getLogger(__name__)

UNPAID_SEND_STATUS = "unpaid"


def is_unpaid_message(msg) -> bool:
    if isinstance(msg, dict):
        return str(msg.get("send_status") or "").strip().lower() == UNPAID_SEND_STATUS
    return str(getattr(msg, "send_status", "") or "").strip().lower() == UNPAID_SEND_STATUS


def llm_visible_messages(msgs) -> list:
    """历史可含 unpaid；喂给模型 / 语种推断时去掉。"""
    return [m for m in (msgs or []) if not is_unpaid_message(m)]


def normalize_message_text_for_dedup(s: str) -> str:
    """判断两条消息是否应视为同一条展示内容（忽略首尾空白与连续空白差异）。"""
    return " ".join((s or "").strip().split())


# 存储前兜底清洗：剥离可能残留的思考过程字段（中英日韩葡西印尼）
_THINK_FIELD_NAMES_RE = (
    r"思考|反思|创意|情绪|亲密度信号|威胁等级|极端|手段模式|策略|人设依据"
    r"|意图|必须回应|新角度|避免|分析|判断|计划|观察|推理"
    r"|理解要点|本轮撩法|创意提示|用户意图|指代|承接"
    r"|understanding|flirt move|creative hint|user intent|must answer|output"
    r"|think|thought|reflect|reflection|creative|creativity|emotion|mood|intent|plan|reason|analysis"
    r"|avoid|observation|strategy|inner|aside|reasoning|brainstorm"
    r"|反省|創造|創意ヒント|今ターンのイチャつき|ユーザー意図|必ず返す点"
    r"|이해 포인트|이번 플러팅|창의 힌트|사용자 의도|반드시 답할 점"
    r"|사고|반성|창의|감정|위협|전략"
    r"|entendimento|flerte desta vez|dica criativa|intenção do usuário|deve responder"
    r"|pensamento|reflexão|criativo|emocao"
    r"|comprensión|coqueteo de este turno|pista creativa|intención del usuario|debe responder"
    r"|pensamiento|reflexión|creativo|emocion"
    r"|poin pemahaman|gerakan flirt giliran ini|petunjuk kreatif|niat pengguna|wajib dijawab"
    r"|berpikir|refleksi|kreatif|emosi"
    r"|user_intent|must_answer|refs|flirt_move|creative_hint|affection_signal"
)
# JSON 键值对：匹配 "字段名": "值", （含多行值）
_STORAGE_JSON_KV_RE = re.compile(
    r'"\s*(?:' + _THINK_FIELD_NAMES_RE + r')"\s*:\s*".*?"\s*,?\s*',
    re.DOTALL | re.IGNORECASE
)
_STORAGE_THINK_LINES_RE = re.compile(
    r'^\s*(?:' + _THINK_FIELD_NAMES_RE + r')[：:]\s*.*$',
    re.MULTILINE | re.IGNORECASE
)
_STORAGE_BRACKET_LABEL_RE = re.compile(
    r'^\s*【(?:' + _THINK_FIELD_NAMES_RE + r'|输出|output)】\s*.*$',
    re.MULTILINE | re.IGNORECASE
)
_STORAGE_JSON_THINK_RE = re.compile(
    r'\{[^{}]*(?:"(?:' + _THINK_FIELD_NAMES_RE + r')")[^{}]*\}',
    re.DOTALL | re.IGNORECASE
)


def _strip_think_fields_for_storage(text: str) -> str:
    """存储前兜底清洗：剥离 JSON KV 对、JSON 思考块、纯文本思考字段行、【标签】行。"""
    if not text:
        return text
    # 如果含"输出"字段，说明上游清洗可能失败，尝试提取
    m = re.search(
        r'(?:输出|output|reply|response)[：:]\s*(.+)',
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # 剥离 JSON KV 对 + JSON 思考块 + 纯文本思考行 + 【标签】行
    cleaned = _STORAGE_JSON_KV_RE.sub("", text)
    cleaned = _STORAGE_JSON_THINK_RE.sub("", cleaned)
    cleaned = _STORAGE_THINK_LINES_RE.sub("", cleaned)
    cleaned = _STORAGE_BRACKET_LABEL_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    # 清理残留的 JSON 括号与碎渣
    if cleaned in ("{", "}", "{}"):
        return ""
    cleaned = re.sub(r'^\s*[{}\[\],]+\s*$', "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(
        r'^\s*"?\s*(?:thought|reflection|creativity|think|reflect|creative|avoid|reasoning)\s*"?\s*[:：].*$',
        "",
        cleaned,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or text.strip()


# ===== ChromaDB ONNX Model Paths =====
_CHROMA_MODEL_DIR = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
_MODEL_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
_FALLBACK_TOTAL_SIZE = 79_300_000  # ~79.3MB

# ===== Embedding Download Status =====
_embedding_status = {
    "state": "idle",      # idle, downloading, ready, error
    "progress": 0.0,      # 0~100
    "message": "",
}


def get_embedding_status() -> dict:
    """获取当前 Embedding 模型下载状态"""
    return _embedding_status.copy()


def _get_total_size() -> int:
    """通过 HEAD 请求获取模型文件总大小"""
    try:
        import httpx
        resp = httpx.head(_MODEL_URL, follow_redirects=True)
        return int(resp.headers.get("content-length", _FALLBACK_TOTAL_SIZE))
    except Exception:
        return _FALLBACK_TOTAL_SIZE


def _monitor_chroma_download():
    """后台线程：监控 ChromaDB ONNX 模型下载进度"""
    global _embedding_status

    # 如果已配置 OpenAI，标记为 ready
    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key:
        _embedding_status.update({
            "state": "ready",
            "progress": 100.0,
            "message": "使用 OpenAI Embedding，无需本地模型",
        })
        return

    total_size = _get_total_size()
    extracted_dir = _CHROMA_MODEL_DIR / "onnx"

    # 检查是否已就绪
    if extracted_dir.exists() and (extracted_dir / "model.onnx").exists():
        _embedding_status.update({
            "state": "ready",
            "progress": 100.0,
            "message": "本地 Embedding 模型已就绪",
        })
        return

    _embedding_status.update({
        "state": "downloading",
        "progress": 0.0,
        "message": "正在准备下载...",
    })

    while True:
        # 检查是否已就绪（可能由另一个线程/进程完成）
        if extracted_dir.exists() and (extracted_dir / "model.onnx").exists():
            _embedding_status.update({
                "state": "ready",
                "progress": 100.0,
                "message": "本地 Embedding 模型已就绪",
            })
            return

        tar_path = _CHROMA_MODEL_DIR / "onnx.tar.gz"
        if tar_path.exists():
            downloaded = tar_path.stat().st_size
            pct = min(100.0, downloaded / total_size * 100) if total_size > 0 else 0
            _embedding_status.update({
                "state": "downloading",
                "progress": round(pct, 1),
                "message": f"正在下载 ONNX 模型... {pct:.1f}%",
            })
        else:
            _embedding_status.update({
                "state": "downloading",
                "progress": 0.0,
                "message": "正在开始下载...",
            })

        time.sleep(1)


def start_embedding_download():
    """启动后台线程：触发模型下载并监控进度"""
    if _embedding_status["state"] == "idle":
        # 启动监控线程
        threading.Thread(target=_monitor_chroma_download, daemon=True).start()
        # 同时触发 DefaultEmbeddingFunction 初始化（实际执行下载）
        def _trigger():
            try:
                ef = DefaultEmbeddingFunction()
                ef(["hello"])  # 调用一次以触发懒加载下载
            except Exception as e:
                logger.warning("Embedding warmup trigger failed: %s", e)
        threading.Thread(target=_trigger, daemon=True).start()


# ===== Embedding =====
_OPENAI_CLIENT = None
_DEFAULT_EF = None


def get_openai_client() -> OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("请设置环境变量 OPENAI_API_KEY 以使用三方 Embedding")
        _OPENAI_CLIENT = OpenAI(api_key=api_key)
    return _OPENAI_CLIENT


def get_default_ef():
    global _DEFAULT_EF
    if _DEFAULT_EF is None:
        _DEFAULT_EF = DefaultEmbeddingFunction()
    return _DEFAULT_EF


def get_embedding(text: str) -> Optional[List[float]]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key:
        try:
            client = get_openai_client()
            resp = client.embeddings.create(
                model="text-embedding-3-small",
                input=text,
            )
            return resp.data[0].embedding
        except Exception:
            return None
    # 未配置 OpenAI Key 时，尝试 ChromaDB 本地 Embedding
    try:
        ef = get_default_ef()
        return ef([text])[0]
    except Exception:
        return None


# ===== ShortTermMemory (Redis 热路径 + PostgreSQL 异步持久化) =====
# 每轮回复注入近聊语料上限（消息级）；实际不足则全量，超过则截最近 N 条
MEMORY_RECENT_MESSAGES = max(1, int(os.getenv("MEMORY_RECENT_MESSAGES", "30") or 30))
_RING_BUFFER_SIZE = max(60, MEMORY_RECENT_MESSAGES)
_ring_buffers: Dict[str, List[Dict]] = {}
_ring_buffer_lock = threading.Lock()


class ShortTermMemory:
    """短期记忆：Redis 桥接优先，PostgreSQL 异步落库；无 Redis 时同步写 PG。
    传入 user_id 时按用户隔离读写（REQ-A2）。"""

    def __init__(self, companion_id: str, user_id: Optional[int] = None):
        self.companion_id = companion_id
        self.user_id = user_id

    def _buf_key(self) -> str:
        if self.user_id is not None:
            return f"{self.companion_id}:{self.user_id}"
        return self.companion_id

    def _msg_filter(self, q):
        q = q.filter(ShortTermMessageORM.companion_id == self.companion_id)
        if self.user_id is not None:
            q = q.filter(ShortTermMessageORM.user_id == self.user_id)
        # 对话举报通过后隐藏
        q = q.filter(
            (ShortTermMessageORM.is_hidden.is_(False))
            | (ShortTermMessageORM.is_hidden.is_(None))
        )
        # flush 可能在清空后把旧消息写回；读路径忽略清空时刻及之前的行
        cleared_at = get_room_cleared_at(self.companion_id, self.user_id)
        if cleared_at is not None:
            q = q.filter(ShortTermMessageORM.created_at > cleared_at)
        return q

    def _drop_cleared_msgs(self, rows: Optional[List[Dict]]) -> List[Dict]:
        """Redis / ring 热读也丢弃清空墓碑及之前的条目（多 worker ring 残留）。"""
        if not rows:
            return []
        cleared_at = get_room_cleared_at(self.companion_id, self.user_id)
        if cleared_at is None:
            return list(rows)
        return [
            m
            for m in rows
            if not message_at_or_before_clear((m or {}).get("timestamp"), cleared_at)
        ]

    def _discard_stale_ring_if_cleared(self, raw_ring: List[Dict], kept: List[Dict]) -> None:
        """清空后本进程或其他 worker 的 ring 仍可能有旧数据：过滤后为空则丢掉该 key。"""
        if not raw_ring or kept:
            return
        key = self._buf_key()
        with _ring_buffer_lock:
            _ring_buffers.pop(key, None)

    def _append_buffer(
        self,
        role: str,
        content: str,
        ts: Optional[datetime] = None,
        msg_id: Optional[int] = None,
        send_status: str = "",
    ):
        ts = ts or datetime.now(timezone.utc)
        entry = {
            "id": msg_id,
            "role": role,
            "content": content,
            "timestamp": ts.isoformat() if ts.tzinfo else ts.replace(tzinfo=timezone.utc).isoformat(),
            "user_id": self.user_id,
            "send_status": (send_status or "").strip()[:20],
        }
        key = self._buf_key()
        with _ring_buffer_lock:
            buf = _ring_buffers.setdefault(key, [])
            buf.append(entry)
            if len(buf) > _RING_BUFFER_SIZE:
                del buf[: len(buf) - _RING_BUFFER_SIZE]

    def _pg_add(self, role: str, content: str, send_status: str = ""):
        with get_db() as db:
            row = ShortTermMessageORM(
                companion_id=self.companion_id,
                user_id=self.user_id,
                role=role,
                content=content,
                send_status=(send_status or "")[:20],
            )
            db.add(row)
            db.flush()
            return row.created_at, row.id

    def add(self, role: str, content: str, send_status: str = ""):
        status = (send_status or "").strip()[:20]
        if bridge_enabled():
            try:
                entry = redis_append_message(
                    self.companion_id,
                    role,
                    content,
                    user_id=self.user_id,
                    send_status=status,
                )
                ts = None
                try:
                    ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                except (TypeError, ValueError, KeyError):
                    ts = datetime.now(timezone.utc)
                # Redis 条目 id 常为 None（未 flush）；ring 用 temp_id 便于前端去重
                out = {
                    "id": entry.get("id") or entry.get("temp_id"),
                    "temp_id": entry.get("temp_id"),
                    "role": role,
                    "content": content,
                    "timestamp": entry.get("timestamp"),
                    "user_id": self.user_id,
                    "send_status": status or entry.get("send_status") or "",
                }
                self._append_buffer(
                    role,
                    content,
                    ts,
                    out.get("id"),
                    send_status=out["send_status"],
                )
                return out
            except Exception as e:
                logger.warning(
                    "Redis chat bridge failed for %s, fallback to PG: %s",
                    self.companion_id,
                    e,
                )

        created, msg_id = self._pg_add(role, content, send_status=status)
        ts_iso = created.isoformat() if created.tzinfo else created.replace(tzinfo=timezone.utc).isoformat()
        out = {
            "id": msg_id,
            "role": role,
            "content": content,
            "timestamp": ts_iso,
            "user_id": self.user_id,
            "send_status": status,
        }
        self._append_buffer(role, content, created, msg_id, send_status=status)
        return out

    def get_last_assistant_content(self) -> Optional[str]:
        if bridge_enabled():
            cached = redis_get_last_assistant(self.companion_id, user_id=self.user_id)
            if cached:
                return cached

        key = self._buf_key()
        with _ring_buffer_lock:
            buf = _ring_buffers.get(key, [])
            for item in reversed(buf):
                if item.get("role") == "assistant" and (item.get("content") or "").strip():
                    return item["content"]

        with get_db() as db:
            row = (
                self._msg_filter(db.query(ShortTermMessageORM))
                .filter(ShortTermMessageORM.role == "assistant")
                .order_by(desc(ShortTermMessageORM.id))
                .limit(1)
                .first()
            )
            if not row or not row.content:
                return None
            return row.content

    def _pg_get_recent(self, n: int, offset: int) -> List[Dict]:
        with get_db() as db:
            msgs = (
                self._msg_filter(db.query(ShortTermMessageORM))
                .order_by(desc(ShortTermMessageORM.id))
                .offset(offset)
                .limit(n)
                .all()
            )

            def _fmt_ts(ts):
                if not ts:
                    return datetime.now(timezone.utc).isoformat()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts.isoformat()

            return [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "timestamp": _fmt_ts(m.created_at),
                    "user_id": m.user_id,
                    "send_status": (getattr(m, "send_status", None) or "").strip(),
                }
                for m in reversed(msgs)
            ]

    @staticmethod
    def _msg_ts_key(m: Dict) -> str:
        return str((m or {}).get("timestamp") or "")

    def _ring_snapshot(self) -> List[Dict]:
        key = self._buf_key()
        with _ring_buffer_lock:
            buf = _ring_buffers.get(key, [])
            return list(buf) if buf else []

    def _prefer_fresher_rows(
        self, redis_rows: Optional[List[Dict]], ring_rows: List[Dict], n: int
    ) -> List[Dict]:
        """Redis 与进程内 ring 取更新的一侧，避免二次打开读到落后热缓存。"""
        candidates: List[List[Dict]] = []
        if redis_rows:
            candidates.append(redis_rows)
        if ring_rows:
            candidates.append(ring_rows)
        if not candidates:
            return []
        best = max(
            candidates,
            key=lambda rows: (self._msg_ts_key(rows[-1]), len(rows)),
        )
        return best[-n:] if len(best) > n else list(best)

    def _msg_dedup_key(self, m: Dict) -> tuple:
        return (
            self._msg_ts_key(m),
            str((m or {}).get("role") or ""),
            normalize_message_text_for_dedup(str((m or {}).get("content") or "")),
        )

    def _merge_pg_behind_hot(self, pg_rows: List[Dict], hot: List[Dict], n: int) -> List[Dict]:
        """用 PG 补更早历史，热缓存更新的尾部原样保留（含未 flush）。"""
        hot_head = self._msg_ts_key(hot[0]) if hot else ""
        hot_keys = {self._msg_dedup_key(m) for m in hot}
        older = [
            m
            for m in pg_rows
            if self._msg_ts_key(m) < hot_head and self._msg_dedup_key(m) not in hot_keys
        ]
        merged = older + list(hot)
        return merged[-n:] if len(merged) > n else merged

    def _replace_hot_with_pg(self, pg_rows: List[Dict]) -> List[Dict]:
        try:
            purge_room_cache(self.companion_id, user_id=self.user_id)
        except Exception:
            pass
        try:
            redis_warm_from_db(self.companion_id, pg_rows, user_id=self.user_id)
        except Exception:
            logger.debug("redis warm after heal failed", exc_info=True)
        key = self._buf_key()
        with _ring_buffer_lock:
            _ring_buffers[key] = pg_rows[-_RING_BUFFER_SIZE:]
        return pg_rows

    def _heal_short_from_pg(self, hot: List[Dict], n: int, offset: int) -> List[Dict]:
        """热缓存不足 n 条时补齐。仅 PG 末条严格更新才覆盖 Redis，避免抹掉未 flush 的新消息。"""
        if offset != 0:
            return hot
        pg_rows = self._pg_get_recent(n, offset)
        if not pg_rows:
            # PG 无可见行时不得把「清空前残留 ring」当成有效历史
            return hot
        if not hot:
            return self._replace_hot_with_pg(pg_rows)
        if self._msg_ts_key(pg_rows[-1]) > self._msg_ts_key(hot[-1]):
            return self._replace_hot_with_pg(pg_rows)
        return self._merge_pg_behind_hot(pg_rows, hot, n)

    def get_recent(self, n: int = MEMORY_RECENT_MESSAGES, offset: int = 0) -> List[Dict]:
        if offset == 0:
            redis_rows: Optional[List[Dict]] = None
            if bridge_enabled():
                redis_rows = redis_get_recent(
                    self.companion_id, n, offset, user_id=self.user_id
                )
            raw_ring = self._ring_snapshot()
            if redis_rows is not None:
                redis_rows = self._drop_cleared_msgs(redis_rows)
            ring_rows = self._drop_cleared_msgs(raw_ring)
            self._discard_stale_ring_if_cleared(raw_ring, ring_rows)

            # Redis 客户端不可用且 ring 空 → 直接 PG
            if redis_rows is None and not ring_rows:
                return self._pg_get_recent(n, offset)

            hot = self._drop_cleared_msgs(
                self._prefer_fresher_rows(redis_rows, ring_rows, n)
            )

            # 热数据不足窗口：PG 补更早历史；未 flush 的新消息不得被覆盖
            if len(hot) < n:
                hot = self._drop_cleared_msgs(
                    self._heal_short_from_pg(hot, n, offset)
                )

            if hot:
                key = self._buf_key()
                with _ring_buffer_lock:
                    _ring_buffers[key] = hot[-_RING_BUFFER_SIZE:]
                return hot

            return self._pg_get_recent(n, offset)

        return self._pg_get_recent(n, offset)

    def get_after(self, after_ts: str, limit: int = 50) -> List[Dict]:
        """返回时间戳严格晚于 after_ts 的消息（升序），用于前端增量同步。"""
        after_ts = (after_ts or "").strip()
        if not after_ts:
            return self.get_recent(limit, 0)

        # 热路径：先看近窗；不足再放宽到 ring 上限
        window = max(limit * 4, min(_RING_BUFFER_SIZE, 120))
        recent = self.get_recent(window, 0)
        newer = [m for m in recent if self._msg_ts_key(m) > after_ts]
        if len(newer) >= limit or len(recent) < window:
            return newer[-limit:] if len(newer) > limit else newer

        # 近窗全是新消息：可能还有更早缺口，但增量场景只需「比本地 tip 更新」
        return newer[-limit:] if len(newer) > limit else newer

    def warm_buffer(self):
        key = self._buf_key()
        if bridge_enabled():
            cached = redis_get_recent(
                self.companion_id, _RING_BUFFER_SIZE, 0, user_id=self.user_id
            )
            if cached:
                with _ring_buffer_lock:
                    _ring_buffers[key] = cached[-_RING_BUFFER_SIZE:]
                return

        with _ring_buffer_lock:
            if _ring_buffers.get(key):
                return
        recent = self.get_recent(_RING_BUFFER_SIZE, offset=0)
        if not recent:
            return
        if bridge_enabled():
            redis_warm_from_db(self.companion_id, recent, user_id=self.user_id)
        with _ring_buffer_lock:
            _ring_buffers[key] = recent[-_RING_BUFFER_SIZE:]

    def get_recent_turns(self, max_turns: int = 20) -> List[Dict]:
        """按轮次获取最近对话，合并同一轮的拆分消息"""
        recent = self.get_recent(n=max_turns * 6)  # 每轮最多6条拆分消息
        if not recent:
            return []

        from services.strategy_image import is_strategy_image_content

        turns = []
        current_role = None
        current_parts = []

        for msg in recent:
            if is_strategy_image_content(msg.get("content")):
                continue
            if is_unpaid_message(msg):
                continue
            if msg["role"] != current_role:
                if current_role:
                    turns.append({
                        "role": current_role,
                        "content": "\n".join(current_parts),
                    })
                current_role = msg["role"]
                current_parts = [msg["content"]]
            else:
                current_parts.append(msg["content"])

        if current_role:
            turns.append({
                "role": current_role,
                "content": "\n".join(current_parts),
            })

        # 限制轮次数
        if len(turns) > max_turns:
            turns = turns[-max_turns:]

        return turns

    def get_turn_count(self) -> int:
        with get_db() as db:
            count = self._msg_filter(db.query(ShortTermMessageORM)).count()
            return count // 2

    def get_total_count(self) -> int:
        if bridge_enabled():
            cached = redis_get_total_count(self.companion_id, user_id=self.user_id)
            if cached is not None and cached > 0:
                return cached
        with get_db() as db:
            return self._msg_filter(db.query(ShortTermMessageORM)).count()

    def clear(self):
        if bridge_enabled():
            redis_clear_messages(self.companion_id, user_id=self.user_id)
        with _ring_buffer_lock:
            _ring_buffers.pop(self._buf_key(), None)
        with get_db() as db:
            q = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == self.companion_id
            )
            if self.user_id is not None:
                q = q.filter(ShortTermMessageORM.user_id == self.user_id)
            q.delete(synchronize_session=False)


# ===== EpisodicMemory (Chroma 向量) =====
class EpisodicMemory:
    """向量情节记忆：Chroma 存储；有 user_id 时按用户分 collection。

    PersistentClient 延迟到首次读写再打开，避免启动时每个智能体占一批 FD。
    """

    def __init__(self, companion_id: str, companion_dir: str, user_id: Optional[int] = None):
        self.companion_id = companion_id
        self.user_id = user_id
        self.persist_dir = os.path.join(companion_dir, "chroma")
        self._client = None
        self._collection = None

    def _collection_name(self) -> str:
        if self.user_id is not None:
            return f"companion_{self.companion_id}_u{self.user_id}"
        return f"companion_{self.companion_id}"

    @property
    def client(self):
        if self._client is None:
            os.makedirs(self.persist_dir, exist_ok=True)
            self._client = get_persistent_client(self.persist_dir)
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self._collection_name(),
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add_episode(self, text: str, metadata: Optional[Dict] = None):
        embedding = get_embedding(text)
        if embedding is None:
            return
        doc_id = str(uuid.uuid4())
        meta = metadata or {}
        meta["timestamp"] = datetime.now(timezone.utc).isoformat()
        if self.user_id is not None:
            meta["user_id"] = int(self.user_id)
        self.collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[meta],
        )

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        embedding = get_embedding(query)
        if embedding is None:
            return []
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        items = []
        for i in range(len(results["documents"][0])):
            items.append({
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
        return items

    def add_turn(
        self,
        user_text: str,
        assistant_text: str,
        pronoun: str = "TA",
        partial: bool = False,
    ):
        combined = f"用户说：{user_text}\n{pronoun}回复：{assistant_text}"
        self.add_episode(
            combined,
            {"type": "dialogue_turn", "partial": bool(partial), "pronoun": pronoun},
        )

# ===== FactMemory (PostgreSQL) =====
class FactMemory:
    """结构化事实记忆：按 companion + user 隔离（user_id 为空时兼容历史 companion 级）。"""

    def __init__(self, companion_id: str, user_id: Optional[int] = None):
        self.companion_id = companion_id
        self.user_id = user_id

    def _fact_query(self, db):
        q = db.query(FactORM).filter(FactORM.companion_id == self.companion_id)
        if self.user_id is not None:
            q = q.filter(FactORM.user_id == self.user_id)
        else:
            q = q.filter(FactORM.user_id.is_(None))
        return q

    def add_facts(self, new_facts: List[str]):
        with get_db() as db:
            existing = {f.fact for f in self._fact_query(db).all()}
            for fact in new_facts:
                fact = fact.strip()
                if fact and fact not in existing:
                    db.add(FactORM(
                        companion_id=self.companion_id,
                        user_id=self.user_id,
                        fact=fact,
                    ))
                    existing.add(fact)

    def get_facts(self) -> List[str]:
        with get_db() as db:
            rows = self._fact_query(db).order_by(FactORM.id).all()
            return [r.fact for r in rows]

    def to_text(self, max_items: int = 20) -> str:
        facts = self.get_facts()
        items = facts[-max_items:] if len(facts) > max_items else facts
        return "\n".join(f"- {item}" for item in items) if items else "（暂无已知事实）"


# ===== RelationSummary (PostgreSQL) =====
class RelationSummary:
    """关系摘要：有 user_id 时写入 user_relation_summaries，否则回退旧表。"""

    def __init__(self, companion_id: str, user_id: Optional[int] = None):
        self.companion_id = companion_id
        self.user_id = user_id

    def _get_row(self) -> dict:
        with get_db() as db:
            if self.user_id is not None:
                row = (
                    db.query(UserRelationSummaryORM)
                    .filter(
                        UserRelationSummaryORM.user_id == self.user_id,
                        UserRelationSummaryORM.companion_id == self.companion_id,
                    )
                    .first()
                )
                if not row:
                    row = UserRelationSummaryORM(
                        user_id=self.user_id,
                        companion_id=self.companion_id,
                    )
                    db.add(row)
                    db.commit()
                    db.refresh(row)
                return {
                    "summary": row.summary,
                    "turns_since_update": row.turns_since_update,
                }
            row = (
                db.query(RelationSummaryORM)
                .filter(RelationSummaryORM.companion_id == self.companion_id)
                .first()
            )
            if not row:
                row = RelationSummaryORM(companion_id=self.companion_id)
                db.add(row)
                db.commit()
                db.refresh(row)
            return {
                "summary": row.summary,
                "turns_since_update": row.turns_since_update,
            }

    def should_update(self) -> bool:
        data = self._get_row()
        return (data.get("turns_since_update") or 0) >= 8

    def update(self, new_summary: str):
        with get_db() as db:
            if self.user_id is not None:
                row = (
                    db.query(UserRelationSummaryORM)
                    .filter(
                        UserRelationSummaryORM.user_id == self.user_id,
                        UserRelationSummaryORM.companion_id == self.companion_id,
                    )
                    .first()
                )
                if row:
                    row.summary = new_summary.strip()
                    row.turns_since_update = 0
                else:
                    db.add(UserRelationSummaryORM(
                        user_id=self.user_id,
                        companion_id=self.companion_id,
                        summary=new_summary.strip(),
                        turns_since_update=0,
                    ))
                return
            row = (
                db.query(RelationSummaryORM)
                .filter(RelationSummaryORM.companion_id == self.companion_id)
                .first()
            )
            if row:
                row.summary = new_summary.strip()
                row.turns_since_update = 0
            else:
                db.add(RelationSummaryORM(
                    companion_id=self.companion_id,
                    summary=new_summary.strip(),
                    turns_since_update=0,
                ))

    def increment_turn(self):
        with get_db() as db:
            if self.user_id is not None:
                row = (
                    db.query(UserRelationSummaryORM)
                    .filter(
                        UserRelationSummaryORM.user_id == self.user_id,
                        UserRelationSummaryORM.companion_id == self.companion_id,
                    )
                    .first()
                )
                if row:
                    row.turns_since_update = (row.turns_since_update or 0) + 1
                else:
                    db.add(UserRelationSummaryORM(
                        user_id=self.user_id,
                        companion_id=self.companion_id,
                        turns_since_update=1,
                    ))
                return
            row = (
                db.query(RelationSummaryORM)
                .filter(RelationSummaryORM.companion_id == self.companion_id)
                .first()
            )
            if row:
                row.turns_since_update = (row.turns_since_update or 0) + 1
            else:
                db.add(RelationSummaryORM(
                    companion_id=self.companion_id,
                    turns_since_update=1,
                ))

    def get_summary(self) -> str:
        data = self._get_row()
        return data.get("summary") or "（你们的关系正在萌芽，每一句对话都让她更靠近你。）"


# ===== CompanionMemory =====
class CompanionMemory:
    """聚合所有记忆层，对外统一接口。

    每个实例应绑定固定 user_id；多用户并发请用 Companion.memory_for(user_id)，
    不要对进程内共享的 Companion.memory 调用 bind_user。
    """

    def __init__(
        self,
        companion_id: str,
        companion_dir: str,
        user_id: Optional[int] = None,
    ):
        self.companion_id = companion_id
        self.companion_dir = companion_dir
        self.short_term = ShortTermMemory(companion_id, user_id=user_id)
        self.episodic = EpisodicMemory(companion_id, companion_dir, user_id=user_id)
        self.facts = FactMemory(companion_id, user_id=user_id)
        self.summary = RelationSummary(companion_id, user_id=user_id)

    def bind_user(self, user_id: Optional[int]) -> None:
        """兼容旧调用：仅重绑本实例。热路径请改用 Companion.memory_for(user_id)。"""
        current = getattr(self.short_term, "user_id", None)
        if current == user_id and getattr(self.facts, "user_id", None) == user_id:
            return
        logger.warning(
            "CompanionMemory.bind_user is unsafe on shared companion.memory; "
            "use Companion.memory_for(user_id) companion=%s user=%s",
            self.companion_id,
            user_id,
        )
        self.short_term = ShortTermMemory(self.companion_id, user_id=user_id)
        self.episodic = EpisodicMemory(self.companion_id, self.companion_dir, user_id=user_id)
        self.facts = FactMemory(self.companion_id, user_id=user_id)
        self.summary = RelationSummary(self.companion_id, user_id=user_id)

    def add_user_message(self, content: str, *, send_status: str = ""):
        self.short_term.add("user", content, send_status=send_status)
        if not (content or "").strip():
            return None
        if is_unpaid_message({"send_status": send_status}):
            return None
        try:
            from services.private_album import on_user_chat_message

            uid = getattr(self.short_term, "user_id", None)
            return on_user_chat_message(uid, self.companion_id)
        except Exception:
            logger.exception(
                "private album increment failed companion=%s", self.companion_id
            )
            return None

    def persist_unpaid_user_messages(self, texts) -> list:
        """超额/欠费：只落用户原文，不扣费、不走相册、不进模型。"""
        stored = []
        for raw in texts or []:
            line = (raw or "").strip()
            if not line:
                continue
            row = self.short_term.add("user", line, send_status=UNPAID_SEND_STATUS)
            if isinstance(row, dict):
                stored.append(
                    {
                        "id": row.get("id") or row.get("temp_id"),
                        "role": "user",
                        "content": row.get("content") or line,
                        "timestamp": row.get("timestamp"),
                        "send_status": UNPAID_SEND_STATUS,
                    }
                )
            else:
                stored.append(
                    {
                        "role": "user",
                        "content": line,
                        "send_status": UNPAID_SEND_STATUS,
                    }
                )
        return stored

    def add_assistant_message(self, content: str):
        if not (content or "").strip():
            return
        # 存储前兜底清洗：剥离可能残留的思考过程字段
        content = _strip_think_fields_for_storage(content)
        if not content.strip():
            return
        last = self.short_term.get_last_assistant_content()
        if last is not None and normalize_message_text_for_dedup(
            content
        ) == normalize_message_text_for_dedup(last):
            return
        self.short_term.add("assistant", content)

    def commit_turn(
        self,
        user_text: str,
        assistant_text: str,
        pronoun: str = "TA",
        partial: bool = False,
    ):
        self.episodic.add_turn(
            user_text, assistant_text, pronoun=pronoun, partial=partial
        )
        self.summary.increment_turn()

    def get_context(self, query: str = "", user_id: Optional[int] = None) -> Dict[str, Any]:
        short = self.short_term
        facts = self.facts
        summary = self.summary
        episodic = self.episodic
        if user_id is not None and short.user_id != user_id:
            short = ShortTermMemory(self.companion_id, user_id=user_id)
            facts = FactMemory(self.companion_id, user_id=user_id)
            summary = RelationSummary(self.companion_id, user_id=user_id)
            episodic = EpisodicMemory(self.companion_id, self.companion_dir, user_id=user_id)
        recent = llm_visible_messages(short.get_recent(MEMORY_RECENT_MESSAGES))
        episodes = []
        if query:
            episodes = episodic.search(query, top_k=5)
        return {
            "recent_dialogue": recent,
            "episodes": episodes,
            "facts": facts.get_facts(),
            "summary": summary.get_summary(),
            "_short_term": short,
        }

    def build_prompt_context(
        self,
        query: str = "",
        max_chars: int = 3500,
        tier: str = "full",
        user_id: Optional[int] = None,
        relation_card_text: str = "",
        truncate_episodes: bool = False,
        lang: str = "zh",
    ) -> str:
        """构建上下文提示。user_id 传入时按该用户读近聊（REQ-A2）。

        规则：每个对话发送下一条前，读取近聊作为 <<RECENT_DIALOGUE>> 语料：
        - 不足 MEMORY_RECENT_MESSAGES（默认 30）条 → 以实际条数为准
        - 超过则只取最近 MEMORY_RECENT_MESSAGES 条
        关系卡/事实不得挤掉该窗口。
        """
        tier = (tier or "full").lower()
        recent_n = MEMORY_RECENT_MESSAGES
        if tier == "compact":
            max_chars = min(max_chars, 800)
            max_facts = 3
            recent_n = min(16, MEMORY_RECENT_MESSAGES)
            episode_limit = 2
            dialogue_reserve = min(500, max_chars // 2)
        else:
            # 理解优先：近聊窗口留足预算，避免关系卡挤掉语料
            full_cap = int(os.getenv("MEMORY_FULL_MAX_CHARS", "6500") or 6500)
            max_chars = min(max_chars, full_cap) if max_chars > 2000 else max_chars
            if max_chars < 4000:
                max_chars = min(full_cap, max(max_chars, 5000))
            max_facts = 6
            episode_limit = 4
            dialogue_reserve = int(os.getenv("MEMORY_DIALOGUE_RESERVE", "4500") or 4500)

        if relation_card_text and truncate_episodes:
            episode_limit = min(episode_limit, 3)

        need_vector = bool(query) and (
            len(query) > 6
            or any(
                k in query
                for k in (
                    "记得",
                    "以前",
                    "那次",
                    "还记得",
                    "上次",
                    "那天",
                    "那个",
                    "他说",
                    "她说",
                    "刚才",
                    "remember",
                    "before",
                    "last time",
                    "that",
                    "记得吗",
                    "思い出",
                    "前に",
                    "기억",
                    "저번",
                    "lembra",
                    "antes",
                    "recuerdas",
                    "ingat",
                    "hatırlıyor",
                    "помнишь",
                    "помниш",
                    "تذكر",
                    "یادت",
                )
            )
        )
        # 有关系卡时仍允许语义检索；勿因 truncate_episodes 直接关闭往事

        ctx = self.get_context(query if need_vector else "", user_id=user_id)
        short = ctx.pop("_short_term", self.short_term)
        other_parts = []

        if relation_card_text:
            # 关系卡可截断；近聊优先
            card = relation_card_text.strip()
            card_cap = int(os.getenv("RELATION_CARD_PROMPT_CHARS", "700"))
            if len(card) > card_cap:
                card = card[: card_cap - 1] + "…"
            other_parts.append(("<<RELATION_CARD>>\n" + card, 85))

        if ctx["summary"] and not relation_card_text:
            other_parts.append(("<<RELATION_SUMMARY>>\n" + ctx["summary"], 100))

        facts = ctx["facts"]
        if facts:
            seen = set()
            unique_facts = []
            fact_cap = max_facts if not relation_card_text else min(max_facts, 4)
            for f in reversed(facts):
                f_norm = f.strip()
                if f_norm and f_norm not in seen:
                    seen.add(f_norm)
                    unique_facts.append(f_norm)
                    if len(unique_facts) >= fact_cap:
                        break
            unique_facts.reverse()
            if unique_facts:
                facts_text = "<<KNOWN_FACTS>>\n" + "\n".join(f"- {f}" for f in unique_facts)
                other_parts.append((facts_text, 80))

        episodes = ctx["episodes"]
        if episodes:
            filtered = [ep for ep in episodes if ep.get("distance", 1.0) < 0.45]
            if filtered:
                ep_lines = []
                for ep in filtered[:episode_limit]:
                    text = ep["text"][:160]
                    ep_lines.append(f"- {text}")
                ep_text = "<<RELATED_EPISODES>>\n" + "\n".join(ep_lines)
                other_parts.append((ep_text, 60))

        dialogue_text = ""
        # 近聊语料：不足 recent_n 条用实际数量；超过则只取最近 recent_n 条
        recent_msgs = llm_visible_messages(list(short.get_recent(recent_n) or []))[-recent_n:]
        if recent_msgs:
            # 稳定标记供 v2 切片；第二行保留可读标题（兼容旧中文标记检索）
            dialogue_lines = ["<<RECENT_DIALOGUE>>", "【最近对话】"]
            for msg in recent_msgs:
                content = (msg.get("content") or "").strip()
                if not content:
                    continue
                who = "他" if msg.get("role") == "user" else "你"
                dialogue_lines.append(f"{who}：{content}")
            if len(dialogue_lines) > 2:
                dialogue_text = "\n".join(dialogue_lines)

        # 先装近聊（理解预算），再装关系卡/事实
        result_parts = []
        total_len = 0
        dlg_budget = min(dialogue_reserve, max_chars) if dialogue_text else 0
        if dialogue_text:
            if len(dialogue_text) <= dlg_budget:
                result_parts.append(dialogue_text)
                total_len += len(dialogue_text)
            else:
                lines = dialogue_text.split("\n")
                kept = [lines[0]]
                kept_len = len(lines[0])
                for line in reversed(lines[1:]):
                    if kept_len + len(line) + 1 <= dlg_budget:
                        kept.insert(1, line)
                        kept_len += len(line) + 1
                    else:
                        break
                if len(kept) > 1:
                    trimmed = "\n".join(kept)
                    result_parts.append(trimmed)
                    total_len += len(trimmed)

        other_budget = max_chars - total_len
        for text, priority in sorted(other_parts, key=lambda x: -x[1]):
            if total_len + len(text) <= max_chars:
                result_parts.append(text)
                total_len += len(text)
                continue
            remaining = max_chars - total_len - 20
            if remaining > 120 and priority >= 80:
                result_parts.append(text[: remaining - 1] + "…")
                total_len = max_chars
                break

        # 近聊放最后，便于模型注意（但已保证装入）
        if dialogue_text and result_parts and (
            result_parts[0].startswith("<<RECENT_DIALOGUE>>")
            or result_parts[0].startswith("【最近对话】")
        ):
            dlg = result_parts.pop(0)
            result_parts.append(dlg)

        return "\n\n".join(result_parts)
