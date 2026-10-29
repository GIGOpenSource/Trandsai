"""快速回复：策略库文案 + 智能体开关配置；用户发消息后命中则回一条。"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from core.database import (
    QuickReplyConfigORM,
    QuickReplyLibraryORM,
    get_db,
    serialize_datetime,
)
from core.i18n import normalize_ui_language
from services.msg_strategy_crud import (
    companions_payload,
    load_companion_id_name_map,
    _normalize_companion_ids,
)

logger = logging.getLogger(__name__)


def normalize_keywords(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("，", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(raw, (list, tuple)):
        out: List[str] = []
        for item in raw:
            s = str(item or "").strip()
            if s:
                out.append(s)
        return out
    raise ValueError("keywords_must_be_list_or_string")


def _keywords_of(row: QuickReplyLibraryORM) -> List[str]:
    kws = row.keywords if isinstance(row.keywords, list) else []
    return [str(k).strip() for k in kws if str(k).strip()]


def _match_keyword(user_text: str, keywords: Sequence[str]) -> bool:
    if not keywords:
        return False
    hay = (user_text or "").casefold()
    if not hay:
        return False
    for kw in keywords:
        needle = kw.casefold()
        if needle and needle in hay:
            return True
    return False


def library_to_dict(
    row: QuickReplyLibraryORM,
    *,
    name_map: Optional[Dict[str, str]] = None,
) -> dict:
    """快回消息策略库单条（对齐埋点策略库形态：文案/语种/智能体/序号/开关）。"""
    cids = _normalize_companion_ids(row.companion_ids)
    nm = name_map or {}
    kws = _keywords_of(row)
    return {
        "id": row.id,
        # 快回文案
        "content": row.content or "",
        # 用户语言
        "language": row.language or "zh",
        # 用户触发词；空数组 = 兜底（任意消息可命中）
        "keywords": kws,
        "trigger_keywords": kws,  # 与 keywords 同值，方便前端展示
        # 作用智能体
        "companion_ids": cids,
        "companions": companions_payload(cids, nm),
        # 序号（越小越优先）
        "sort_order": int(row.sort_order or 0),
        # 是否生效
        "is_active": bool(row.is_active),
        "created_by": row.created_by or "",
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }


def enrich_library_rows(db: Session, rows: Iterable[QuickReplyLibraryORM]) -> List[dict]:
    row_list = list(rows)
    all_ids: List[str] = []
    seen = set()
    for row in row_list:
        for cid in _normalize_companion_ids(row.companion_ids):
            if cid not in seen:
                seen.add(cid)
                all_ids.append(cid)
    name_map = load_companion_id_name_map(db, all_ids)
    return [library_to_dict(r, name_map=name_map) for r in row_list]


def apply_library_fields(row: QuickReplyLibraryORM, data: dict) -> None:
    if "content" in data:
        row.content = (data.get("content") or "").strip()
    if "language" in data:
        row.language = (data.get("language") or "zh").strip()[:10]
    # 用户触发词：keywords / trigger_keywords
    if "keywords" in data or "trigger_keywords" in data:
        raw = data.get("keywords") if "keywords" in data else data.get("trigger_keywords")
        row.keywords = normalize_keywords(raw)
    if "companion_ids" in data or "companions" in data:
        raw = data.get("companion_ids")
        if raw is None:
            raw = data.get("companions")
        row.companion_ids = _normalize_companion_ids(raw)
    # 序号：sort_order / sort_no / seq
    if "sort_order" in data or "sort_no" in data or "seq" in data:
        raw = data.get("sort_order")
        if raw is None:
            raw = data.get("sort_no")
        if raw is None:
            raw = data.get("seq")
        row.sort_order = int(raw or 0)
    if "is_active" in data:
        row.is_active = bool(data.get("is_active"))
    if "created_by" in data:
        row.created_by = (data.get("created_by") or "").strip()[:64]


def config_to_dict(
    row: QuickReplyConfigORM,
    *,
    companion_name: str = "",
) -> dict:
    return {
        "id": row.id,
        "companion_id": row.companion_id,
        "companion_name": companion_name or "",
        "name": row.name or "",
        "is_active": bool(row.is_active),
        "priority": int(row.priority or 0),
        "created_by": row.created_by or "",
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }


def enrich_config_rows(db: Session, rows: Iterable[QuickReplyConfigORM]) -> List[dict]:
    row_list = list(rows)
    ids = [r.companion_id for r in row_list if r.companion_id]
    name_map = load_companion_id_name_map(db, ids)
    return [
        config_to_dict(r, companion_name=name_map.get(r.companion_id or "", ""))
        for r in row_list
    ]


def apply_config_fields(row: QuickReplyConfigORM, data: dict) -> None:
    if "name" in data:
        row.name = (data.get("name") or "").strip()[:120]
    if "is_active" in data:
        row.is_active = bool(data.get("is_active"))
    if "priority" in data:
        row.priority = int(data.get("priority") or 0)
    if "created_by" in data:
        row.created_by = (data.get("created_by") or "").strip()[:64]


def _last_used_key(user_id: int, companion_id: str) -> str:
    return f"quick_reply:last:{int(user_id)}:{str(companion_id)[:8]}"


def _get_last_used_id(user_id: int, companion_id: str) -> Optional[int]:
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        if not r:
            return None
        raw = r.get(_last_used_key(user_id, companion_id))
        if raw is None:
            return None
        return int(raw)
    except Exception:
        return None


def _set_last_used_id(user_id: int, companion_id: str, reply_id: int) -> None:
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        if not r:
            return
        r.setex(_last_used_key(user_id, companion_id), 7 * 24 * 3600, str(int(reply_id)))
    except Exception:
        logger.debug("quick_reply last_used set failed", exc_info=True)


def _companion_disabled(db: Session, companion_id: str) -> bool:
    """仅当该智能体在 configs 里显式关闭时才不触发。没有配置行 = 以库绑定为准。"""
    row = (
        db.query(QuickReplyConfigORM)
        .filter(QuickReplyConfigORM.companion_id == companion_id)
        .first()
    )
    if row is None:
        return False
    return not bool(row.is_active)


def ensure_quick_reply_configs(db: Session, companion_ids: Sequence[str]) -> int:
    """库绑了智能体就补 configs 行（默认开启）。已有行不改，避免覆盖人工关闭。"""
    ids: List[str] = []
    for raw in companion_ids or []:
        cid = str(raw or "").strip()[:8]
        if cid and cid not in ids:
            ids.append(cid)
    if not ids:
        return 0
    existing = {
        str(r.companion_id)
        for r in db.query(QuickReplyConfigORM)
        .filter(QuickReplyConfigORM.companion_id.in_(ids))
        .all()
    }
    n = 0
    for cid in ids:
        if cid in existing:
            continue
        db.add(QuickReplyConfigORM(companion_id=cid, name="", is_active=True))
        n += 1
    if n:
        db.flush()
    return n


def backfill_quick_reply_configs(db: Session) -> int:
    ids: List[str] = []
    seen = set()
    for row in db.query(QuickReplyLibraryORM).filter(QuickReplyLibraryORM.is_active.is_(True)).all():
        for raw in row.companion_ids or []:
            cid = str(raw or "").strip()[:8]
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)
    return ensure_quick_reply_configs(db, ids)


def _row_has_companion(row: QuickReplyLibraryORM, companion_id: str) -> bool:
    cid = (companion_id or "").strip()[:8]
    if not cid:
        return False
    for x in row.companion_ids or []:
        s = str(x).strip()
        if s == companion_id or s[:8] == cid:
            return True
    return False


def _query_library_for_companion(
    db: Session, companion_id: str
) -> List[QuickReplyLibraryORM]:
    """生效且 companion_ids 含该智能体；不按 language 过滤（关键词命中即可）。"""
    cid = (companion_id or "").strip()[:8]
    base = db.query(QuickReplyLibraryORM).filter(
        QuickReplyLibraryORM.is_active.is_(True),
    )
    hinted: List[QuickReplyLibraryORM] = []
    try:
        hinted = (
            base.filter(QuickReplyLibraryORM.companion_ids.contains([cid]))
            .order_by(
                QuickReplyLibraryORM.sort_order.asc(),
                QuickReplyLibraryORM.id.asc(),
            )
            .all()
        )
    except Exception:
        logger.debug("quick_reply jsonb contains failed companion=%s", cid, exc_info=True)
    out = [r for r in hinted if _row_has_companion(r, cid)]
    if out:
        return out
    rows = base.order_by(
        QuickReplyLibraryORM.sort_order.asc(),
        QuickReplyLibraryORM.id.asc(),
    ).all()
    return [r for r in rows if _row_has_companion(r, cid)]


def pick_quick_reply(
    *,
    companion_id: str,
    language: str,
    user_text: str,
    user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    选出一条话术。
    条件：库绑定该智能体且生效；configs 仅在显式关闭时拦截。
    关键词命中不要求库 language 与用户语种一致（同语种优先）。
    """
    cid = (companion_id or "").strip()[:8]
    if not cid:
        return None
    lang = normalize_ui_language(language or "en")
    text = (user_text or "").strip()
    with get_db() as db:
        if _companion_disabled(db, cid):
            logger.info("[quick_reply] skip disabled config companion=%s", cid)
            return None
        ensure_quick_reply_configs(db, [cid])

        rows = _query_library_for_companion(db, cid)
        if not rows:
            logger.info("[quick_reply] miss no library companion=%s lang=%s", cid, lang)
            return None

        keyword_hits: List[QuickReplyLibraryORM] = []
        catch_all: List[QuickReplyLibraryORM] = []
        for row in rows:
            kws = _keywords_of(row)
            if not kws:
                catch_all.append(row)
            elif _match_keyword(text, kws):
                keyword_hits.append(row)

        pool = keyword_hits or catch_all
        if not pool:
            logger.info(
                "[quick_reply] miss no keyword companion=%s lang=%s rows=%s",
                cid,
                lang,
                len(rows),
            )
            return None

        same_lang = [
            r for r in pool
            if normalize_ui_language(r.language or "en") == lang
        ]
        if same_lang:
            pool = same_lang

        last_id = _get_last_used_id(user_id, cid) if user_id else None
        if last_id is not None and len(pool) > 1:
            filtered = [r for r in pool if int(r.id) != int(last_id)]
            if filtered:
                pool = filtered

        best = min(int(r.sort_order or 0) for r in pool)
        tier = [r for r in pool if int(r.sort_order or 0) == best]
        chosen = random.choice(tier)
        data = library_to_dict(chosen)
        if user_id:
            _set_last_used_id(user_id, cid, int(chosen.id))
        return data


# 兼容旧测试/引用名
quick_reply_to_dict = library_to_dict


def persist_picked_quick_reply(
    companion,
    *,
    user_id: int,
    companion_id: str,
    user_text: str,
    picked: Dict[str, Any],
) -> str:
    """把命中的快回写入记忆并记一轮，返回正文；失败仍返回正文。"""
    content = (picked.get("content") or "").strip()
    if not content:
        return ""
    try:
        mem = companion.memory_for(user_id)
        mem.add_assistant_message(content)
        mem.commit_turn(user_text, content, partial=False)
        companion.state.turns = int(getattr(companion.state, "turns", 0) or 0) + 1
        companion.save_state(user_id=user_id)
    except Exception:
        logger.exception(
            "quick_reply persist failed user=%s companion=%s id=%s",
            user_id,
            companion_id,
            picked.get("id"),
        )
    try:
        from core.database import UserCompanionStateORM, get_db as _get_db

        with _get_db() as db:
            ucs = (
                db.query(UserCompanionStateORM)
                .filter(
                    UserCompanionStateORM.user_id == user_id,
                    UserCompanionStateORM.companion_id == companion_id,
                )
                .first()
            )
            if ucs:
                ucs.updated_at = datetime.now(timezone.utc)
    except Exception:
        pass
    return content


async def try_deliver_quick_reply(
    *,
    websocket,
    companion,
    user_id: int,
    companion_id: str,
    user_text: str,
    language: str,
    send_json,
) -> bool:
    """
    命中则推送一条 assistant 消息并落库，返回 True（调用方应跳过 AI）。
    无人命中返回 False，继续 AI / 其它分支。
    """
    picked = pick_quick_reply(
        companion_id=companion_id,
        language=language,
        user_text=user_text,
        user_id=user_id,
    )
    if not picked:
        return False
    content = (picked.get("content") or "").strip()
    if not content:
        return False

    await send_json(websocket, {"type": "typing"})
    delivered = await send_json(
        websocket,
        {
            "type": "message",
            "role": "assistant",
            "text": content,
            "quick_reply": True,
            "quick_reply_id": picked.get("id"),
        },
    )

    persist_picked_quick_reply(
        companion,
        user_id=user_id,
        companion_id=companion_id,
        user_text=user_text,
        picked=picked,
    )
    try:
        from services.user_chat_unread import note_assistant_unread

        note_assistant_unread(
            int(user_id),
            str(companion_id)[:8],
            delivered_live=bool(delivered),
        )
    except Exception:
        logger.debug(
            "quick_reply unread note skipped user=%s companion=%s",
            user_id,
            companion_id,
            exc_info=True,
        )

    try:
        from services.im_admin import broadcast_admin_im

        await broadcast_admin_im(
            user_id,
            companion_id,
            {
                "type": "message",
                "role": "assistant",
                "text": content,
                "quick_reply": True,
                "quick_reply_id": picked.get("id"),
            },
        )
    except Exception:
        logger.debug("quick_reply admin broadcast failed", exc_info=True)

    logger.info(
        "[quick_reply] sent user=%s companion=%s id=%s lang=%s",
        user_id,
        companion_id,
        picked.get("id"),
        picked.get("language"),
    )
    return True
