"""策略中心：触发词不分割、拆绑定、判重辅助。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.strategy_center import (
    others_after_detach,
    parse_companion_ids_from_body,
    parse_trigger_keyword,
    trigger_keyword_display,
)


class TestParseTriggerKeyword(unittest.TestCase):
    def test_does_not_split_comma(self):
        self.assertEqual(parse_trigger_keyword("哈哈,笑死"), ["哈哈,笑死"])

    def test_empty_is_fallback(self):
        self.assertEqual(parse_trigger_keyword(""), [])
        self.assertEqual(parse_trigger_keyword(None), [])
        self.assertEqual(parse_trigger_keyword("  "), [])

    def test_list_kept_as_items(self):
        self.assertEqual(parse_trigger_keyword(["哈哈", "笑死"]), ["哈哈", "笑死"])

    def test_display_empty(self):
        self.assertEqual(trigger_keyword_display([]), "")
        self.assertEqual(trigger_keyword_display(["只要你"]), "只要你")


class TestDetach(unittest.TestCase):
    def test_remove_one_keep_others(self):
        others, found = others_after_detach(["aaa11111", "bbb22222"], "aaa11111")
        self.assertTrue(found)
        self.assertEqual(others, ["bbb22222"])

    def test_remove_last(self):
        others, found = others_after_detach(["aaa11111"], "aaa11111")
        self.assertTrue(found)
        self.assertEqual(others, [])

    def test_missing(self):
        others, found = others_after_detach(["aaa11111"], "zzz99999")
        self.assertFalse(found)
        self.assertEqual(others, ["aaa11111"])


class TestParseCompanionIds(unittest.TestCase):
    def test_ids_and_single(self):
        ids = parse_companion_ids_from_body(
            {"companion_ids": ["abcdefgh"], "companion_id": "ijklmnop"}
        )
        self.assertEqual(ids, ["abcdefgh", "ijklmnop"])

    def test_from_companions_objects(self):
        ids = parse_companion_ids_from_body(
            {"companions": [{"id": "abcdefgh"}, {"id": "ijklmnop"}]}
        )
        self.assertEqual(ids, ["abcdefgh", "ijklmnop"])


if __name__ == "__main__":
    unittest.main()
