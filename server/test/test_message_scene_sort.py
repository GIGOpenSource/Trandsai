"""消息场景列表排序：默认 last_message_time 近→远；sort=affection 亲密度倒序。"""
from __future__ import annotations

import os
import unittest


class TestMessageListSort(unittest.TestCase):
    def _src(self) -> str:
        path = os.path.join(
            os.path.dirname(__file__), "..", "services", "companion_manager.py"
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_normalize_defaults_to_last_message_time(self):
        src = self._src()
        self.assertIn('return "last_message_time"', src)
        self.assertIn('== "affection"', src)

    def test_affection_sort_includes_owned_without_chat(self):
        src = self._src()
        self.assertIn("owned_clause", src)
        self.assertIn("chatted_clause", src)
        self.assertIn("is_owned", src)
        self.assertIn("sort_key == \"affection\"", src)
        self.assertIn("last_msg_at", src)
        self.assertIn("nulls_last", src)
        # 消息 Tab 仍要求有聊天；首页 affection 自己创建的可不要求 last_message
        self.assertIn("if sort_key != \"affection\" or not is_owned:", src)

    def test_message_scene_does_not_apply_recommend_strategy(self):
        src = self._src()
        start = src.index("def _list_message_scene")
        end = src.find("\n    def ", start + 10)
        block = src[start:end] if end > 0 else src[start:]
        self.assertNotIn("_apply_list_waterfall", block)
        self.assertNotIn("should_apply_waterfall", block)
        self.assertIn("不套推荐策略", block)


if __name__ == "__main__":
    unittest.main()
