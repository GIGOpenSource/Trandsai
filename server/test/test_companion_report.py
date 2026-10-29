"""智能体举报服务单测。"""
from __future__ import annotations

import inspect
import os
import sys
import unittest

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.companion_report import (  # noqa: E402
    REPORT_REASON_CODES,
    filter_companion_list_items,
    list_excluded_companion_ids_for_user,
    list_report_reasons,
    reason_meta,
)


class TestCompanionReportReasons(unittest.TestCase):
    def test_all_reason_codes_present(self):
        self.assertEqual(
            REPORT_REASON_CODES,
            {"minor_safety", "sensitive", "disturbing", "copyright", "other"},
        )

    def test_list_reasons_have_stable_ids(self):
        rows = list_report_reasons()
        self.assertEqual(len(rows), 5)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, [1, 2, 3, 4, 5])

    def test_reason_meta_by_id(self):
        meta = reason_meta(reason_id=1)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["reason_code"], "minor_safety")
        self.assertEqual(meta["priority"], "P0")

    def test_minor_safety_is_p0(self):
        meta = reason_meta("minor_safety")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["priority"], "P0")


class TestCreatedListSkipsOutreachExclude(unittest.TestCase):
    def test_excluded_helper_accepts_include_outreach(self):
        src = inspect.getsource(list_excluded_companion_ids_for_user)
        self.assertIn("include_outreach", src)
        self.assertIn('("block", "dislike")', src)
        self.assertIn("func.lower", src)

    def test_filter_helper_forwards_include_outreach(self):
        src = inspect.getsource(filter_companion_list_items)
        self.assertIn("include_outreach=include_outreach", src)

    def test_my_created_sql_skips_outreach(self):
        path = os.path.join(os.path.dirname(__file__), "..", "services", "companion_manager.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def _list_other_scenes")
        block = src[start : start + 2500]
        self.assertIn("include_outreach=(is_created is not True)", block)

    def test_bcompanions_filter_skips_outreach_for_created(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "companions.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("include_outreach=(is_created is not True)", src)

    def test_dislike_and_block_zero_affection(self):
        path = os.path.join(os.path.dirname(__file__), "..", "services", "outreach_phase3.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def set_outreach_preference")
        block = src[start : start + 3500]
        self.assertIn('if action in ("block", "dislike"):', block)
        self.assertIn("UserCompanionStateORM.affection: 0", block)
        # clear 必须硬删偏好行，否则消息列表仍会隐藏
        self.assertIn(".delete(synchronize_session=False)", block)
        self.assertIn('action == "clear"', block)

    def test_exclude_only_block_dislike_not_clear(self):
        src = inspect.getsource(list_excluded_companion_ids_for_user)
        self.assertIn('("block", "dislike")', src)
        # clear 不应进入排除列表条件
        self.assertNotIn('"clear"', src)
        self.assertNotIn("'clear'", src)


if __name__ == "__main__":
    unittest.main()
