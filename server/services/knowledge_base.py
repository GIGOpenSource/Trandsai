import os
import shutil
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field
from sqlalchemy import desc

from core.chroma_client import evict_client, get_persistent_client
from core.database import KnowledgeEntryORM, get_db
from services.memory import get_embedding

logger = logging.getLogger(__name__)


CATEGORIES = [
    "pua_tactics",
    "red_flags",
    "love_bombing",
    "gaslighting",
    "breadcrumbing",
    "narcissist",
    "emotional_blackmail",
    "other",
]


class KnowledgeEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=5, max_length=5000)
    category: str = Field(default="other", pattern=r"^(" + "|".join(CATEGORIES) + r")$")
    tags: List[str] = Field(default_factory=list)
    source: str = Field(default="manual")
    language: str = Field(default="zh")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class KnowledgeBase:
    def __init__(self, persist_dir: str = "memory/knowledge_base"):
        self.persist_dir = persist_dir
        os.makedirs(self.persist_dir, exist_ok=True)

        # 彻底修复 ChromaDB 兼容性问题（KeyError: '_type'）；client 走全局复用（S-18）
        try:
            self.client = get_persistent_client(self.persist_dir)
            # 使用最安全的创建方式，避免旧元数据冲突
            self.collection = self.client.get_or_create_collection(
                name="knowledge_base",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("KnowledgeBase 初始化成功 (collection 已就绪)")
        except Exception as e:
            logger.error("KnowledgeBase 初始化失败: %s。尝试重置...", str(e))
            try:
                # 如果失败，删除旧目录并重新创建（先逐出缓存的 client，避免复用失效句柄）
                evict_client(self.persist_dir)
                if os.path.exists(self.persist_dir):
                    shutil.rmtree(self.persist_dir)
                os.makedirs(self.persist_dir, exist_ok=True)
                self.client = get_persistent_client(self.persist_dir)
                self.collection = self.client.create_collection(
                    name="knowledge_base",
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info("KnowledgeBase 已重置并成功初始化")
            except Exception as e2:
                logger.error("KnowledgeBase 完全初始化失败: %s", str(e2))
                raise

    def _vector_meta(self, entry: Union[KnowledgeEntry, Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(entry, KnowledgeEntry):
            return {
                "title": entry.title,
                "category": entry.category,
                "language": entry.language,
                "source": entry.source,
                "created_at": entry.created_at,
            }
        return {
            "title": entry.get("title") or "",
            "category": entry.get("category") or "other",
            "language": entry.get("language") or "zh",
            "source": entry.get("source") or "manual",
            "created_at": entry.get("created_at") or "",
        }

    def _upsert_vector(self, entry_id: str, content: str, meta: Dict[str, Any]) -> bool:
        embedding = get_embedding(content)
        if embedding is None:
            return False
        payload = {
            "ids": [entry_id],
            "embeddings": [embedding],
            "documents": [content],
            "metadatas": [meta],
        }
        try:
            if hasattr(self.collection, "upsert"):
                self.collection.upsert(**payload)
            else:
                try:
                    self.collection.delete(ids=[entry_id])
                except Exception:
                    pass
                self.collection.add(**payload)
            return True
        except Exception as e:
            logger.warning("Knowledge vector upsert failed for %s: %s", entry_id, e)
            return False

    def add_entry(self, entry_data: dict) -> KnowledgeEntry:
        entry = KnowledgeEntry(**entry_data)
        self._upsert_vector(entry.id, entry.content, self._vector_meta(entry))
        with get_db() as db:
            db.add(KnowledgeEntryORM(
                id=entry.id,
                title=entry.title,
                content=entry.content,
                category=entry.category,
                tags=entry.tags,
                source=entry.source,
                language=entry.language,
            ))
        return entry

    def update_entry(self, entry_id: str, entry_data: dict) -> Optional[KnowledgeEntry]:
        with get_db() as db:
            row = db.query(KnowledgeEntryORM).filter(KnowledgeEntryORM.id == entry_id).first()
            if not row:
                return None
            if "title" in entry_data:
                row.title = entry_data["title"]
            if "content" in entry_data:
                row.content = entry_data["content"]
            if "category" in entry_data:
                row.category = entry_data["category"]
            if "language" in entry_data:
                row.language = entry_data["language"]
            if "tags" in entry_data:
                row.tags = entry_data["tags"]
            if "source" in entry_data:
                row.source = entry_data["source"]
            db.flush()
            updated_entry = KnowledgeEntry(
                id=row.id,
                title=row.title,
                content=row.content,
                category=row.category,
                tags=row.tags or [],
                source=row.source,
                language=row.language,
                created_at=row.created_at.isoformat() if row.created_at else "",
            )
        self._upsert_vector(
            entry_id,
            updated_entry.content,
            self._vector_meta(updated_entry),
        )
        return updated_entry

    def delete_entry(self, entry_id: str) -> bool:
        with get_db() as db:
            row = db.query(KnowledgeEntryORM).filter(KnowledgeEntryORM.id == entry_id).first()
            if not row:
                return False
            db.delete(row)
        try:
            self.collection.delete(ids=[entry_id])
        except Exception as e:
            logger.warning("Knowledge vector delete failed for %s: %s", entry_id, e)
        return True

    def list_entries(
        self,
        category: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[Dict]:
        items, _ = self.list_entries_page(
            category=category,
            language=language,
            page=1,
            page_size=10_000,
        )
        return items

    def list_entries_page(
        self,
        category: Optional[str] = None,
        language: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
    ) -> Tuple[List[Dict], int]:
        page = max(1, int(page or 1))
        page_size = min(200, max(1, int(page_size or 10)))
        with get_db() as db:
            q = db.query(KnowledgeEntryORM)
            if category:
                q = q.filter(KnowledgeEntryORM.category == category)
            if language:
                q = q.filter(KnowledgeEntryORM.language == language)
            total = q.count()
            rows = (
                q.order_by(desc(KnowledgeEntryORM.created_at))
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            items = [
                {
                    "id": r.id,
                    "title": r.title,
                    "content": r.content,
                    "category": r.category,
                    "tags": r.tags or [],
                    "source": r.source,
                    "language": r.language,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                }
                for r in rows
            ]
        return items, int(total)

    def search_entries(self, query: str, top_k: int = 5) -> List[Dict]:
        embedding = get_embedding(query)
        if embedding is None:
            return []
        try:
            total = int(self.collection.count() or 0)
        except Exception:
            total = 0
        if total <= 0:
            return []
        n_results = max(1, min(int(top_k or 5), total))
        try:
            results = self.collection.query(
                query_embeddings=[embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.warning("Knowledge vector search failed: %s", e)
            return []

        docs = (results or {}).get("documents") or [[]]
        metas = (results or {}).get("metadatas") or [[]]
        ids = (results or {}).get("ids") or [[]]
        dists = (results or {}).get("distances") or [[]]
        if not docs or not docs[0]:
            return []

        items = []
        for i in range(len(docs[0])):
            meta = (metas[0][i] if metas and metas[0] else {}) or {}
            items.append({
                "id": ids[0][i] if ids and ids[0] else "",
                "content": docs[0][i],
                "title": meta.get("title", ""),
                "category": meta.get("category", ""),
                "language": meta.get("language", ""),
                "source": meta.get("source", ""),
                "distance": dists[0][i] if dists and dists[0] else None,
            })
        return items

    def clear_all(self) -> int:
        """清空所有知识条目及其向量数据"""
        count = 0
        with get_db() as db:
            count = db.query(KnowledgeEntryORM).delete(synchronize_session=False)
            db.commit()
        try:
            self.collection.delete(where={})
            logger.info("[KnowledgeBase] 已清空所有知识条目，共 %s 条", count)
        except Exception as e:
            logger.warning("Knowledge vector clear failed: %s", e)
        return count or 0

    def dedupe_by_title(self) -> int:
        """同标题保留最新一条，删除其余（多实例启动重复导入场景）。"""
        removed = 0
        with get_db() as db:
            rows = (
                db.query(KnowledgeEntryORM)
                .order_by(desc(KnowledgeEntryORM.created_at))
                .all()
            )
            seen = set()
            drop_ids = []
            for row in rows:
                title = (row.title or "").strip()
                if not title:
                    continue
                if title in seen:
                    drop_ids.append(row.id)
                else:
                    seen.add(title)
            for eid in drop_ids:
                row = db.query(KnowledgeEntryORM).filter(KnowledgeEntryORM.id == eid).first()
                if row:
                    db.delete(row)
                    removed += 1
            if removed:
                db.commit()
        for eid in drop_ids:
            try:
                self.collection.delete(ids=[eid])
            except Exception:
                pass
        if removed:
            logger.info("[KnowledgeBase] 去重删除同标题旧条目: %s 条", removed)
        return removed

    def reindex_vectors(self) -> Dict[str, int]:
        """用 DB 全文重建向量索引（覆盖空向量/漏写）。"""
        entries = self.list_entries()
        try:
            self.collection.delete(where={})
        except Exception as e:
            logger.warning("Knowledge vector clear before reindex failed: %s", e)

        indexed = 0
        skipped = 0
        for entry in entries:
            embedding = get_embedding(entry["content"])
            if embedding is None:
                skipped += 1
                continue
            try:
                payload = {
                    "ids": [entry["id"]],
                    "embeddings": [embedding],
                    "documents": [entry["content"]],
                    "metadatas": [self._vector_meta(entry)],
                }
                if hasattr(self.collection, "upsert"):
                    self.collection.upsert(**payload)
                else:
                    self.collection.add(**payload)
                indexed += 1
            except Exception as e:
                logger.warning("Knowledge reindex failed for %s: %s", entry["id"], e)
                skipped += 1
        logger.info(
            "[KnowledgeBase] 向量重建完成 total=%s indexed=%s skipped=%s",
            len(entries),
            indexed,
            skipped,
        )
        return {
            "total": len(entries),
            "indexed": indexed,
            "skipped": skipped,
        }

    def ensure_vectors_synced(self) -> Dict[str, Any]:
        """DB 有条目但向量为空/偏少时自动重建。"""
        with get_db() as db:
            db_count = int(db.query(KnowledgeEntryORM).count() or 0)
        try:
            vec_count = int(self.collection.count() or 0)
        except Exception:
            vec_count = 0
        if db_count <= 0:
            return {"db_count": 0, "vec_count": vec_count, "reindexed": False}
        if vec_count >= db_count:
            return {
                "db_count": db_count,
                "vec_count": vec_count,
                "reindexed": False,
                "already_synced": True,
            }
        stats = self.reindex_vectors()
        stats.update({
            "db_count": db_count,
            "vec_count_before": vec_count,
            "reindexed": True,
        })
        return stats

    def get_stats(self) -> Dict[str, Any]:
        with get_db() as db:
            total = db.query(KnowledgeEntryORM).count()
            cats = {}
            for cat in CATEGORIES:
                cats[cat] = db.query(KnowledgeEntryORM).filter(KnowledgeEntryORM.category == cat).count()
        try:
            vector_count = int(self.collection.count() or 0)
        except Exception:
            vector_count = 0
        return {
            "total_entries": total,
            "vector_count": vector_count,
            "categories": list(cats.keys()),
        }


def import_cultural_knowledge() -> int:
    """将文化常识导入知识库，返回导入数量；并去重 + 同步向量。"""
    from services.culture_data import get_cultural_knowledge_entries

    entries = get_cultural_knowledge_entries()
    imported = 0
    with get_db() as db:
        existing_titles = {r.title for r in db.query(KnowledgeEntryORM.title).all()}
    for entry in entries:
        if entry["title"] in existing_titles:
            continue
        knowledge_base.add_entry(entry)
        existing_titles.add(entry["title"])
        imported += 1
    if imported:
        logger.info("[KnowledgeBase] 自动导入文化常识: %s 条", imported)

    removed = knowledge_base.dedupe_by_title()
    sync = knowledge_base.ensure_vectors_synced()
    if removed or sync.get("reindexed"):
        logger.info(
            "[KnowledgeBase] 导入后维护 removed=%s sync=%s",
            removed,
            sync,
        )
    return imported


# 全局实例
knowledge_base = KnowledgeBase()
