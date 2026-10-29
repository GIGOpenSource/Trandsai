"""动态策略触发工具：按埋点事件匹配策略文案。

当前为伪执行（print / logger）；真实发消息逻辑后续接入。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.auth import verify_token
from core.database import CompanionStrategyORM, get_db
from services.companion_content import product_event_meta, strategy_to_dict
from services.strategy_types import get_event_keys, list_strategy_types

logger = logging.getLogger(__name__)


class StrategyTrigger:
    """埋点事件 → 策略匹配 → 伪触发（print）。"""

    @staticmethod
    def list_events() -> List[Dict[str, str]]:
        with get_db() as db:
            rows = list_strategy_types(db, active_only=True)
            if rows:
                return [
                    {"event_key": r.event_key, "name_zh": r.name_zh or ""}
                    for r in rows
                ]
        from services.strategy_types import DEFAULT_STRATEGY_TYPES
        return [{"event_key": x["event_key"], "name_zh": x["name_zh"]} for x in DEFAULT_STRATEGY_TYPES]

    @staticmethod
    def event_keys() -> set[str]:
        with get_db() as db:
            return get_event_keys(db, active_only=True)

    @staticmethod
    def is_valid_event(event_key: str) -> bool:
        return (event_key or "").strip() in StrategyTrigger.event_keys()

    @staticmethod
    def parse_user_id(token: str) -> Optional[int]:
        """从用户 x-token / Bearer 解析 user_id。"""
        return verify_token((token or "").strip())

    @staticmethod
    def find_strategies(
        trigger_event: str,
        *,
        companion_id: Optional[str] = None,
        language: Optional[str] = None,
        active_only: bool = True,
    ) -> List[dict]:
        event = (trigger_event or "").strip()
        if not event:
            return []
        with get_db() as db:
            q = db.query(CompanionStrategyORM).filter(
                CompanionStrategyORM.trigger_event == event,
            )
            if companion_id:
                q = q.filter(CompanionStrategyORM.companion_id == companion_id.strip())
            if language:
                q = q.filter(CompanionStrategyORM.language == language.strip())
            if active_only:
                q = q.filter(CompanionStrategyORM.is_active.is_(True))
            rows = q.order_by(
                CompanionStrategyORM.sort_order.asc(),
                CompanionStrategyORM.id.asc(),
            ).all()
            return [strategy_to_dict(r) for r in rows]

    @classmethod
    def dispatch(
        cls,
        trigger_event: str,
        token: str,
        *,
        companion_id: Optional[str] = None,
        language: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """伪触发：解析 token，匹配策略并 print（不发消息）。"""
        event = (trigger_event or "").strip()
        if not cls.is_valid_event(event):
            return {"ok": False, "reason": "invalid_event", "event": event}

        user_id = cls.parse_user_id(token)
        if user_id is None:
            return {"ok": False, "reason": "invalid_token", "event": event}

        resolved_lang = (language or "").strip()
        if not resolved_lang:
            with get_db() as db:
                from core.database import UserORM
                u = db.query(UserORM).filter(UserORM.id == user_id).first()
                resolved_lang = ((getattr(u, "language", None) or "") if u else "").strip()

        strategies = cls.find_strategies(
            event,
            companion_id=companion_id,
            language=resolved_lang or None,
            active_only=True,
        )
        meta = product_event_meta(event)
        payload = {
            "ok": True,
            "user_id": user_id,
            "event": event,
            "event_name_zh": meta.get("name_zh", event),
            "matched": len(strategies),
            "strategies": strategies,
            "extra": extra or {},
        }

        # 伪执行：后续替换为真实发消息
        line = (
            f"[strategy_trigger] event={event} ({meta.get('name_zh', '')}) "
            f"user_id={user_id} matched={len(strategies)}"
        )
        print(line, flush=True)
        logger.info(line)
        for s in strategies:
            detail = (
                f"[strategy_trigger] -> companion={s.get('companion_id')} "
                f"lang={s.get('language')} strategy_id={s.get('id')} "
                f"content={((s.get('content') or '')[:80])!r}"
            )
            print(detail, flush=True)
            logger.info(detail)

        return payload
