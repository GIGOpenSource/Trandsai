"""亲密度计算：不依赖数据库，避免空关系卡/整数列把值打成 0。"""
from __future__ import annotations

import os


def coalesce_affection_write(existing, incoming) -> float:
    """关系卡默认同步：空卡 affection=0 不得把已涨的列打回 0。"""
    try:
        old = float(existing or 0)
    except (TypeError, ValueError):
        old = 0.0
    try:
        new = float(incoming if incoming is not None else 0)
    except (TypeError, ValueError):
        new = 0.0
    if new <= 0 and old > 0:
        return old
    return new


def catch_up_stalled_affection(aff: float, turns: int) -> float:
    """聊过天但亲密度仍≈0（整数截断 / 空关系卡打回 0）时按轮数补齐。"""
    try:
        aff_f = float(aff or 0)
    except (TypeError, ValueError):
        aff_f = 0.0
    turns_n = int(turns or 0)
    if turns_n < 1:
        return aff_f
    if aff_f >= 0.05:
        if turns_n >= 12 and aff_f < max(5.0, turns_n * 0.35):
            return min(45.0, max(aff_f, turns_n * 0.9))
        return aff_f
    # 与 agent._calculate_affection_delta(signal=up) 同公式
    base_amt = float(os.getenv("AFFECTION_BASE", "0.65"))
    taper = float(os.getenv("AFFECTION_TAPER", "30") or 30)
    if taper <= 0:
        taper = 30.0
    rebuilt = 0.0
    for _ in range(min(turns_n, 120)):
        rebuilt = max(0.0, min(100.0, rebuilt + round(base_amt / (1 + rebuilt / taper), 4)))
    return rebuilt
