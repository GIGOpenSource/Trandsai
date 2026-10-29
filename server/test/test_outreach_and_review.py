"""Outreach / preference unit-ish tests (no live DB required for pure helpers)."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_outreach_modules_parse():
    for name in ("services/outreach.py", "services/outreach_phase3.py", "services/outreach_scheduler.py"):
        src = (ROOT / name).read_text(encoding="utf-8")
        ast.parse(src)


def test_silence_bucket_logic_threshold():
    """达到阈值即匹配（非 ±tol 窄窗）。"""
    specs = [(1, "silence_d1", 8), (3, "silence_d3", 12), (7, "silence_d7", 24), (14, "silence_d14", 36)]

    def match(idle_h: float, already: set[str]) -> str | None:
        for days, job_type, _ in specs:
            target_h = days * 24
            if idle_h >= target_h and job_type not in already:
                return job_type
        return None

    assert match(20, set()) is None
    assert match(24, set()) == "silence_d1"
    assert match(40, set()) == "silence_d1"  # 错过 ±tol 窄窗仍可补发 D1
    assert match(80, {"silence_d1"}) == "silence_d3"
    assert match(200, {"silence_d1", "silence_d3", "silence_d7"}) == "silence_d14"


def test_comment_auto_review_rules():
    from services.moments import _auto_review_comment

    assert _auto_review_comment("今天天气真好")["approved"] is True
    assert _auto_review_comment("加微信123")["approved"] is False
    assert _auto_review_comment("aaaaaaaaaa")["approved"] is False
    assert _auto_review_comment("联系我 13800138000")["approved"] is False
    emoji_only = _auto_review_comment("😂😂😂")
    assert emoji_only["approved"] is False
    assert "不能为空" not in emoji_only["toast"]
    punct_only = _auto_review_comment("!!!")
    assert punct_only["approved"] is False
    mixed = _auto_review_comment("好看😊")
    assert mixed["approved"] is True


def test_devices_uses_redis_verify_import():
    src = (ROOT / "api/devices.py").read_text(encoding="utf-8")
    assert "from core.auth import verify_token" in src
    assert "from api.auth import verify_user_token" not in src


def test_admin_clear_moments_decorated():
    src = (ROOT / "api/admin.py").read_text(encoding="utf-8")
    assert "@router.delete(\"/api/admin/moments\"" in src or "@router.delete('/api/admin/moments'" in src


def test_dislike_and_block_zero_affection():
    src = (ROOT / "services/outreach_phase3.py").read_text(encoding="utf-8")
    assert "if action in (\"block\", \"dislike\"):" in src
    assert "UserCompanionStateORM.affection: 0" in src
    assert "c.state.affection = 0.0" in src
