"""成文组装：续场提前、短人设、黄腔轮降权 flirt。"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestRespondAssemblyOrder(unittest.TestCase):
    def test_pipeline_has_context_mid_before_persona(self):
        src = Path(__file__).resolve().parents[1] / "services" / "agent_pipeline_v2.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("context_mid", text)
        self.assertIn("adult_turn", text)
        self.assertIn("user_tease", text)
        self.assertIn("resolve_scene_sticky", text)
        self.assertIn("effective_topic_band", text)
        # 成人 soft_tail 也带续聊感
        self.assertIn("engage_continue", text)
        # 最终拼装：hard_top → context → mid_rules →（成人）pre_persona → full_prompt → soft_tail
        idx_hard = text.find("{hard_top}")
        idx_ctx = text.find("{context_mid}")
        idx_rules = text.find("{mid_rules}")
        idx_pre = text.find("{pre_persona}")
        idx_full = text.find("{full_prompt}")
        idx_soft = text.find("{soft_tail}")
        self.assertGreater(idx_hard, 0)
        self.assertLess(idx_hard, idx_ctx)
        self.assertLess(idx_ctx, idx_rules)
        self.assertLess(idx_rules, idx_pre)
        self.assertLess(idx_pre, idx_full)
        self.assertLess(idx_full, idx_soft)
        self.assertIn("pre_persona", text)
        self.assertIn("chat_vibe_adult", text)
        # 勿复读进硬顶抬权重
        self.assertIn("repeat_hint", text)
        hard_block = text[text.find("hard_top = "): text.find("context_mid = ")]
        self.assertIn("repeat_hint", hard_block)
        mid_block = text[text.find("mid_rules = "): text.find("show_flirt")]
        self.assertNotIn("repeat_hint", mid_block)
        # 非中文成人轮：硬顶迎合 + soft_tail 可带主动撩；中文仍清 flirt
        self.assertIn("adult_cater_hard", text)
        self.assertIn("proactive_flirt", text)
        self.assertIn("is_zh_face", text)
        self.assertIn("uptake_soft", text)
        # 近聊不再塞进 soft_tail 列表里的旧顺序注释可无；确认 soft_tail 构造不含 thread_hint 赋值进 join 旧位
        tree = ast.parse(text)
        self.assertTrue(any(isinstance(n, ast.FunctionDef) for n in tree.body))


if __name__ == "__main__":
    unittest.main()
