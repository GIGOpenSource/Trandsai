"""漏斗单元格：人数（占登录用户百分比）。"""
from __future__ import annotations

from typing import Optional


def format_funnel_count_pct(count: int, login_n: int, *, is_login_col: bool = False) -> str:
    """人数（百分比），分母为当日登录用户数。登录列为 0 时百分比为 —。"""
    n = int(count or 0)
    denom = int(login_n or 0)
    if denom <= 0:
        return f"{n}（—）"
    if is_login_col:
        return f"{n}（100%）"
    pct = round(100.0 * n / denom, 2)
    if pct == 0:
        pct_s = "0%"
    elif pct == 100:
        pct_s = "100%"
    else:
        pct_s = f"{pct:.2f}%"
    return f"{n}（{pct_s}）"


def pct_of_login(count: int, login_n: int) -> Optional[float]:
    denom = int(login_n or 0)
    if denom <= 0:
        return None
    return round(100.0 * int(count or 0) / denom, 2)
