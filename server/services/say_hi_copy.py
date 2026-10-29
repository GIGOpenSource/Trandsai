"""Say Hi 文案库：管理端 CRUD + 用户端按语种随机抽取。"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.database import SayHiCopyORM, serialize_datetime
from core.i18n import normalize_ui_language
from services.companion_content import SUPPORTED_LANGUAGES, supported_language_options

# 库里该语种没有生效文案时的兜底（与历史硬编码一致）
_FALLBACK_BY_LANG = {
    "zh": "你好，看到你的动态很有感触，想和你聊聊。",
    "en": "Hey, your post caught my eye — would love to chat.",
    "ja": "こんにちは。投稿を見て話しかけたくなりました。",
    "ko": "안녕하세요. 게시물을 보고 인사하고 싶었어요.",
}


def normalize_say_hi_language(raw: Optional[str]) -> str:
    """规范成短码；不在支持列表时仍返回短码（便于入库），空则 en。"""
    code = normalize_ui_language(raw or "") or ""
    if not code:
        return "en"
    return code


def copy_to_dict(row: SayHiCopyORM) -> Dict[str, Any]:
    return {
        "id": row.id,
        "content": row.content or "",
        "language": row.language or "",
        "sort_order": int(row.sort_order or 0),
        "is_active": bool(row.is_active),
        "remark": row.remark or "",
        "created_by": row.created_by or "",
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }


def list_say_hi_copies(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    language: Optional[str] = None,
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    q = db.query(SayHiCopyORM)
    if language and language.strip():
        q = q.filter(SayHiCopyORM.language == normalize_say_hi_language(language))
    if is_active is not None:
        q = q.filter(SayHiCopyORM.is_active == bool(is_active))
    if keyword and keyword.strip():
        like = f"%{keyword.strip()}%"
        q = q.filter(SayHiCopyORM.content.ilike(like))
    total = q.count()
    rows = (
        q.order_by(
            SayHiCopyORM.language.asc(),
            SayHiCopyORM.sort_order.asc(),
            SayHiCopyORM.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [copy_to_dict(r) for r in rows], total


def create_say_hi_copy(
    db: Session,
    *,
    content: str,
    language: str,
    sort_order: int = 0,
    is_active: bool = True,
    remark: str = "",
    created_by: str = "",
) -> SayHiCopyORM:
    row = SayHiCopyORM(
        content=content.strip(),
        language=normalize_say_hi_language(language),
        sort_order=int(sort_order or 0),
        is_active=bool(is_active),
        remark=(remark or "").strip()[:256],
        created_by=(created_by or "").strip()[:64],
    )
    db.add(row)
    db.flush()
    return row


def update_say_hi_copy(row: SayHiCopyORM, data: dict) -> SayHiCopyORM:
    if "content" in data and data.get("content") is not None:
        row.content = str(data.get("content") or "").strip()
    if "language" in data and data.get("language") is not None:
        row.language = normalize_say_hi_language(str(data.get("language") or ""))
    if "sort_order" in data and data.get("sort_order") is not None:
        try:
            row.sort_order = int(data.get("sort_order") or 0)
        except (TypeError, ValueError):
            row.sort_order = 0
    if "is_active" in data and data.get("is_active") is not None:
        row.is_active = bool(data.get("is_active"))
    if "remark" in data and data.get("remark") is not None:
        row.remark = str(data.get("remark") or "").strip()[:256]
    return row


def pick_say_hi_content(db: Session, lang: str) -> str:
    """按 Accept-Language 对应短码，从生效文案中随机抽一条。

    优先同语种；没有则 en → zh → 任意生效语种；库全空再用硬编码兜底。
    """
    primary = normalize_say_hi_language(lang)
    candidates_order = [primary]
    for extra in ("en", "zh"):
        if extra not in candidates_order:
            candidates_order.append(extra)

    for code in candidates_order:
        rows = (
            db.query(SayHiCopyORM.content)
            .filter(
                SayHiCopyORM.language == code,
                SayHiCopyORM.is_active.is_(True),
                SayHiCopyORM.content != "",
            )
            .all()
        )
        texts = [str(r[0]).strip() for r in rows if (r[0] or "").strip()]
        if texts:
            return random.choice(texts)

    any_rows = (
        db.query(SayHiCopyORM.content)
        .filter(SayHiCopyORM.is_active.is_(True), SayHiCopyORM.content != "")
        .all()
    )
    texts = [str(r[0]).strip() for r in any_rows if (r[0] or "").strip()]
    if texts:
        return random.choice(texts)

    return _FALLBACK_BY_LANG.get(primary) or _FALLBACK_BY_LANG["en"]


def filter_options() -> Dict[str, Any]:
    return {
        "languages": supported_language_options(),
        "is_active": [
            {"value": True, "label": "生效"},
            {"value": False, "label": "停用"},
        ],
    }
