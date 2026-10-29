"""审核驳回：解析 reason_id + remark（文案由前端传入，后端不维护原因字典）。"""
from __future__ import annotations

from typing import Any, Optional, Tuple

_REJECT_REASON_MAX = 200


def parse_reject_body(
    *,
    reason_id: Any,
    remark: Any = None,
) -> Tuple[Optional[int], str, Optional[str]]:
    """解析 body → (reason_id, remark文案, error_key)。

    - reason_id：必填正整数（不做选项白名单），落库 reject_reason_id
    - remark：前端传入的展示文案，落库 reject_reason
    """
    try:
        rid = int(reason_id)
    except (TypeError, ValueError):
        return None, "", "invalid_reject_reason"
    if rid < 1:
        return None, "", "invalid_reject_reason"

    note = ""
    if remark is not None:
        note = str(remark).strip()
    return rid, note[:_REJECT_REASON_MAX], None
