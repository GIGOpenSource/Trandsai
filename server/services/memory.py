import os
import threading
import time
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from openai import OpenAI
from sqlalchemy import desc

from core.database import (
    FactORM,
    RelationSummaryORM,
    ShortTermMessageORM,
    get_db,
)

logger = logging.getLogger(__name__)


def normalize_message_text_for_dedup(s: str) -> str:
    """判断两条消息是否应视为同一条展示内容（忽略首尾空白与连续空白差异）。"""
    return " ".join((s or "").strip().split())


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


# ===== ShortTermMemory (MySQL) =====
class ShortTermMemory:
    """短期记忆：保留最近 15 轮原始对话（每轮含 user + assistant，最多 30 条消息）"""

    def __init__(self, companion_id: str, user_id: Optional[int] = None):
        self.companion_id = companion_id
        self.user_id = user_id

    def add(self, role: str, content: str):
        with get_db() as db:
            db.add(ShortTermMessageORM(
                companion_id=self.companion_id,
                user_id=self.user_id,
                role=role,
                content=content,
            ))

    def get_last_assistant_content(self) -> Optional[str]:
        """最近一条 AI 气泡的原文，用于发送前去重。"""
        with get_db() as db:
            query = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == self.companion_id,
                ShortTermMessageORM.role == "assistant",
            )
            if self.user_id:
                query = query.filter(ShortTermMessageORM.user_id == self.user_id)
            row = query.order_by(desc(ShortTermMessageORM.id)).limit(1).first()
            if not row or not row.content:
                return None
            return row.content

    def get_recent(self, n: int = 60, offset: int = 0) -> List[Dict]:
        with get_db() as db:
            query = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == self.companion_id
            )
            if self.user_id:
                query = query.filter(ShortTermMessageORM.user_id == self.user_id)
            msgs = query.order_by(desc(ShortTermMessageORM.id)).offset(offset).limit(n).all()

            def _fmt_ts(ts):
                if not ts:
                    return datetime.now(timezone.utc).isoformat()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts.isoformat()

            return [
                {"role": m.role, "content": m.content, "timestamp": _fmt_ts(m.created_at)}
                for m in reversed(msgs)
            ]

    def get_recent_turns(self, max_turns: int = 20) -> List[Dict]:
        """按轮次获取最近对话，合并同一轮的拆分消息"""
        recent = self.get_recent(n=max_turns * 6)  # 每轮最多6条拆分消息
        if not recent:
            return []

        turns = []
        current_role = None
        current_parts = []

        for msg in recent:
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
            query = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == self.companion_id
            )
            if self.user_id:
                query = query.filter(ShortTermMessageORM.user_id == self.user_id)
            count = query.count()
            return count // 2

    def get_total_count(self) -> int:
        """返回短期记忆的实际总记录数（含分段消息）"""
        with get_db() as db:
            query = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == self.companion_id
            )
            if self.user_id:
                query = query.filter(ShortTermMessageORM.user_id == self.user_id)
            return query.count()

    def clear(self):
        with get_db() as db:
            query = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == self.companion_id
            )
            if self.user_id:
                query = query.filter(ShortTermMessageORM.user_id == self.user_id)
            query.delete(synchronize_session=False)


# ===== EpisodicMemory (Chroma 向量) =====
class EpisodicMemory:
    """向量情节记忆：Chroma 存储，按智能体隔离 collection"""

    def __init__(self, companion_id: str, companion_dir: str):
        self.companion_id = companion_id
        self.persist_dir = os.path.join(companion_dir, "chroma")
        os.makedirs(self.persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=f"companion_{companion_id}",
            metadata={"hnsw:space": "cosine"},
        )

    def add_episode(self, text: str, metadata: Optional[Dict] = None):
        embedding = get_embedding(text)
        if embedding is None:
            return
        doc_id = str(uuid.uuid4())
        meta = metadata or {}
        meta["timestamp"] = datetime.now(timezone.utc).isoformat()
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

    def add_turn(self, user_text: str, assistant_text: str):
        combined = f"用户说：{user_text}\n她回复：{assistant_text}"
        self.add_episode(combined, {"type": "dialogue_turn"})


# ===== FactMemory (MySQL) =====
class FactMemory:
    """结构化事实记忆：MySQL 存储"""

    def __init__(self, companion_id: str):
        self.companion_id = companion_id

    def add_facts(self, new_facts: List[str]):
        with get_db() as db:
            existing = {
                f.fact for f in db.query(FactORM).filter(
                    FactORM.companion_id == self.companion_id
                ).all()
            }
            for fact in new_facts:
                fact = fact.strip()
                if fact and fact not in existing:
                    db.add(FactORM(companion_id=self.companion_id, fact=fact))
                    existing.add(fact)

    def get_facts(self) -> List[str]:
        with get_db() as db:
            rows = (
                db.query(FactORM)
                .filter(FactORM.companion_id == self.companion_id)
                .order_by(FactORM.id)
                .all()
            )
            return [r.fact for r in rows]

    def to_text(self, max_items: int = 20) -> str:
        facts = self.get_facts()
        items = facts[-max_items:] if len(facts) > max_items else facts
        return "\n".join(f"- {item}" for item in items) if items else "（暂无已知事实）"


# ===== RelationSummary (MySQL) =====
class RelationSummary:
    """关系摘要：每 8 轮自动更新一句温馨摘要"""

    def __init__(self, companion_id: str):
        self.companion_id = companion_id

    def _get_row(self) -> dict:
        with get_db() as db:
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
            # 在 session 内提取值，避免 detached instance 错误
            return {
                "summary": row.summary,
                "turns_since_update": row.turns_since_update,
            }

    def should_update(self) -> bool:
        data = self._get_row()
        return (data.get("turns_since_update") or 0) >= 8

    def update(self, new_summary: str):
        with get_db() as db:
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
    """聚合所有记忆层，对外统一接口"""

    def __init__(self, companion_id: str, companion_dir: str, user_id: Optional[int] = None):
        self.companion_id = companion_id
        self.user_id = user_id
        self.short_term = ShortTermMemory(companion_id, user_id)
        self.episodic = EpisodicMemory(companion_id, companion_dir)
        self.facts = FactMemory(companion_id)
        self.summary = RelationSummary(companion_id)

    def add_user_message(self, content: str):
        self.short_term.add("user", content)

    def add_assistant_message(self, content: str):
        if not (content or "").strip():
            return
        last = self.short_term.get_last_assistant_content()
        if last is not None and normalize_message_text_for_dedup(
            content
        ) == normalize_message_text_for_dedup(last):
            return
        self.short_term.add("assistant", content)

    def commit_turn(self, user_text: str, assistant_text: str):
        self.episodic.add_turn(user_text, assistant_text)
        self.summary.increment_turn()

    def get_context(self, query: str = "") -> Dict[str, Any]:
        recent = self.short_term.get_recent(60)
        episodes = []
        if query:
            episodes = self.episodic.search(query, top_k=5)
        return {
            "recent_dialogue": recent,
            "episodes": episodes,
            "facts": self.facts.get_facts(),
            "summary": self.summary.get_summary(),
        }

    def build_prompt_context(self, query: str = "", max_chars: int = 3500) -> str:
        """构建上下文提示，按优先级放入内容并控制总长度"""
        ctx = self.get_context(query)
        parts = []

        # === 1. 关系摘要（优先级最高，固定保留）===
        if ctx["summary"]:
            parts.append(("【关系摘要】\n" + ctx["summary"], 100))

        # === 2. 已知事实（去重，保留最新的10条）===
        facts = ctx["facts"]
        if facts:
            seen = set()
            unique_facts = []
            for f in reversed(facts):
                f_norm = f.strip()
                if f_norm and f_norm not in seen:
                    seen.add(f_norm)
                    unique_facts.append(f_norm)
                    if len(unique_facts) >= 10:
                        break
            unique_facts.reverse()
            if unique_facts:
                facts_text = "【关于他的已知事实】\n" + "\n".join(f"- {f}" for f in unique_facts)
                parts.append((facts_text, 80))

        # === 3. 相关往事（距离阈值过滤，最多3条，每条截断）===
        episodes = ctx["episodes"]
        if episodes:
            # 只保留相似度足够高的（距离 < 0.4，cosine 距离）
            filtered = [ep for ep in episodes if ep.get("distance", 1.0) < 0.4]
            if filtered:
                ep_lines = []
                for ep in filtered[:3]:
                    text = ep["text"][:120]  # 截断到120字
                    ep_lines.append(f"- {text}")
                ep_text = "【相关往事】\n" + "\n".join(ep_lines)
                parts.append((ep_text, 60))

        # === 4. 最近对话（按轮合并，动态长度控制）===
        recent_turns = self.short_term.get_recent_turns(max_turns=20)
        if recent_turns:
            dialogue_lines = ["【最近对话】"]
            for msg in recent_turns:
                who = "他" if msg["role"] == "user" else "你"
                dialogue_lines.append(f"{who}：{msg['content']}")
            dialogue_text = "\n".join(dialogue_lines)
            parts.append((dialogue_text, 70))

        # === 组装并截断 ===
        # 按优先级顺序拼接
        result_parts = []
        total_len = 0
        for text, priority in parts:
            # 如果加入后会超出限制，且这是低优先级的对话历史，则截断
            if total_len + len(text) > max_chars and priority < 90:
                remaining = max_chars - total_len - 50  # 留50字缓冲
                if remaining > 100 and text.startswith("【最近对话】"):
                    # 截断对话历史：保留最新的部分
                    lines = text.split("\n")
                    # 从末尾开始保留
                    kept = [lines[0]]  # 保留标题
                    kept_len = len(lines[0])
                    for line in reversed(lines[1:]):
                        if kept_len + len(line) + 1 <= remaining:
                            kept.insert(1, line)
                            kept_len += len(line) + 1
                        else:
                            break
                    if len(kept) > 1:
                        text = "\n".join(kept)
                        result_parts.append(text)
                        total_len += len(text)
                # 其他部分如果超限则跳过
                continue
            result_parts.append(text)
            total_len += len(text)

        return "\n\n".join(result_parts)
