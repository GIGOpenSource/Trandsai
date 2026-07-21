import os
import shutil
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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

    def add_entry(self, entry_data: dict) -> KnowledgeEntry:
        entry = KnowledgeEntry(**entry_data)
        # 存入向量库
        embedding = get_embedding(entry.content)
        if embedding is not None:
            self.collection.add(
                ids=[entry.id],
                embeddings=[embedding],
                documents=[entry.content],
                metadatas=[{
                    "title": entry.title,
                    "category": entry.category,
                    "language": entry.language,
                    "source": entry.source,
                    "created_at": entry.created_at,
                }],
            )
        # 存入 MySQL
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
            )
        # 更新向量库
        try:
            embedding = get_embedding(updated_entry.content)
            if embedding is not None:
                self.collection.update(
                    ids=[entry_id],
                    embeddings=[embedding],
                    documents=[updated_entry.content],
                    metadatas=[{
                        "title": updated_entry.title,
                        "category": updated_entry.category,
                        "language": updated_entry.language,
                        "source": updated_entry.source,
                        "created_at": updated_entry.created_at,
                    }],
                )
        except Exception as e:
            logger.warning("Knowledge vector update failed for %s: %s", entry_id, e)
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

    def list_entries(self, category: Optional[str] = None, language: Optional[str] = None) -> List[Dict]:
        with get_db() as db:
            q = db.query(KnowledgeEntryORM).order_by(desc(KnowledgeEntryORM.created_at))
            if category:
                q = q.filter(KnowledgeEntryORM.category == category)
            if language:
                q = q.filter(KnowledgeEntryORM.language == language)
            rows = q.all()
            return [
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

    def search_entries(self, query: str, top_k: int = 5) -> List[Dict]:
        embedding = get_embedding(query)
        if embedding is None:
            return []

    def clear_all(self) -> int:
        """清空所有知识条目及其向量数据"""
        count = 0
        with get_db() as db:
            count = db.query(KnowledgeEntryORM).delete(synchronize_session=False)
            db.commit()
        try:
            # 清空 Chroma collection 中的所有数据
            self.collection.delete(where={})
            logger.info("[KnowledgeBase] 已清空所有知识条目，共 %s 条", count)
        except Exception as e:
            logger.warning("Knowledge vector clear failed: %s", e)
        return count or 0
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        items = []
        for i in range(len(results["documents"][0])):
            meta = results["metadatas"][0][i]
            items.append({
                "id": results["ids"][0][i],
                "content": results["documents"][0][i],
                "title": meta.get("title", ""),
                "category": meta.get("category", ""),
                "language": meta.get("language", ""),
                "source": meta.get("source", ""),
                "distance": results["distances"][0][i],
            })
        return items

    def get_stats(self) -> Dict[str, Any]:
        with get_db() as db:
            total = db.query(KnowledgeEntryORM).count()
            cats = {}
            for cat in CATEGORIES:
                cats[cat] = db.query(KnowledgeEntryORM).filter(KnowledgeEntryORM.category == cat).count()
        return {
            "total_entries": total,
            "categories": list(cats.keys()),
        }


def import_cultural_knowledge() -> int:
    """将文化常识导入知识库，返回导入数量"""
    from services.culture_data import get_cultural_knowledge_entries

    entries = get_cultural_knowledge_entries()
    imported = 0
    with get_db() as db:
        existing_titles = {r.title for r in db.query(KnowledgeEntryORM.title).all()}
    for entry in entries:
        if entry["title"] in existing_titles:
            continue
        knowledge_base.add_entry(entry)
        imported += 1
    if imported:
        logger.info("[KnowledgeBase] 自动导入文化常识: %s 条", imported)
    return imported


# 全局实例
knowledge_base = KnowledgeBase()
