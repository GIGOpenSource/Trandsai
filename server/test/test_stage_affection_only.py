"""关系阶段 / 初见：只认有效亲密度，不认轮次。"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if "sqlalchemy" not in sys.modules:
    sys.modules["sqlalchemy"] = MagicMock()
    sys.modules["sqlalchemy.orm"] = MagicMock()
if "core.database" not in sys.modules:
    _db = types.ModuleType("core.database")
    for _name in (
        "ConfigGroupORM",
        "UserORM",
        "AgentConfigORM",
        "CompanionAgentConfigORM",
    ):
        setattr(_db, _name, type(_name, (object,), {}))
    _db.get_db = MagicMock()
    sys.modules["core.database"] = _db

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.dialogue_phase2 import (
    resolve_relation_stage,
    should_inject_first_turn,
)
from services.agent_utils import build_system_prompt


class TestStageByAffectionOnly(unittest.TestCase):
    def test_high_aff_not_stranger_even_turn0(self):
        # 广告户托底 80：首轮也应是 intimate，不能因 turns=0 变 stranger
        self.assertEqual(resolve_relation_stage(0, 80), "intimate")
        self.assertEqual(resolve_relation_stage(0, 50), "intimate")
        self.assertEqual(resolve_relation_stage(0, 30), "ambiguous")
        self.assertEqual(resolve_relation_stage(0, 15), "familiar")
        self.assertEqual(resolve_relation_stage(0, 5), "stranger")

    def test_turns_ignored(self):
        self.assertEqual(resolve_relation_stage(100, 5), "stranger")
        self.assertEqual(resolve_relation_stage(1, 80), "intimate")

    def test_first_turn_inject_by_aff(self):
        self.assertTrue(should_inject_first_turn(0))
        self.assertTrue(should_inject_first_turn(7.9))
        self.assertFalse(should_inject_first_turn(8))
        self.assertFalse(should_inject_first_turn(80))


class TestFirstTurnPrompt(unittest.TestCase):
    def test_high_aff_skips_first_turn_block(self):
        with patch("services.agent_utils._get_agent_config", return_value={}):
            low = build_system_prompt(
                {"name": "A", "age": 22, "personality": "温柔"},
                "zh",
                turns=0,
                tier="respond",
                affection=0,
            )
            high = build_system_prompt(
                {"name": "A", "age": 22, "personality": "温柔"},
                "zh",
                turns=0,
                tier="respond",
                affection=80,
            )
        self.assertIn("刚认识", low)
        self.assertNotIn("刚认识", high)


if __name__ == "__main__":
    unittest.main()
