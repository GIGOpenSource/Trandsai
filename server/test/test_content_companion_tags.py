"""内容管理智能体列表：运营标签筛选。"""
from __future__ import annotations

import os
import sys
import unittest

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.companion_content import parse_tag_filter


class TestParseTagFilter(unittest.TestCase):
    def test_repeat_and_comma(self):
        self.assertEqual(parse_tag_filter(["romance", "中东24"]), ["romance", "中东24"])
        self.assertEqual(parse_tag_filter("romance,中东24"), ["romance", "中东24"])
        self.assertEqual(parse_tag_filter("romance，中东24"), ["romance", "中东24"])

    def test_empty_skipped(self):
        self.assertEqual(parse_tag_filter(None), [])
        self.assertEqual(parse_tag_filter(["", "romance", "romance"]), ["romance"])

    def test_sql_uses_jsonb_any(self):
        path = os.path.join(os.path.dirname(__file__), "..", "services", "companion_content.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def apply_companion_tag_filter")
        block = src[start : start + 600]
        self.assertIn('op("?|")', block)

    def test_list_api_accepts_tags(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "admin_content.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("async def admin_content_list_companions")
        block = src[start : start + 3500]
        self.assertIn("tags: Optional[List[str]]", block)
        self.assertIn("apply_companion_tag_filter(q, tags)", block)

    def test_list_item_includes_tags(self):
        path = os.path.join(os.path.dirname(__file__), "..", "services", "companion_content.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def companion_list_item")
        block = src[start : src.index("def profile_payload")]
        self.assertIn('"tags": list(getattr(row, "tags", None) or [])', block)


if __name__ == "__main__":
    unittest.main()
