"""列表/详情对话轮数：只读当前用户 user_companion_states，不回退全局 companion_states。"""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from types import SimpleNamespace

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.companion_manager import (
    CompanionManager,
    _batch_user_companion_states_with_db,
    _counters_from_user_row,
)


class TestCountersFromUserRow(unittest.TestCase):
    def test_none_is_zero(self):
        self.assertEqual(_counters_from_user_row(None), {"affection": 0.0, "turns": 0})

    def test_reads_user_row(self):
        row = SimpleNamespace(affection=6.5, turns=12)
        self.assertEqual(_counters_from_user_row(row)["turns"], 12)
        self.assertEqual(_counters_from_user_row(row)["affection"], 6.5)

    def test_null_fields_zero(self):
        row = SimpleNamespace(affection=None, turns=None)
        self.assertEqual(_counters_from_user_row(row), {"affection": 0.0, "turns": 0})


class TestBatchHasNoGlobalFallback(unittest.TestCase):
    def test_batch_helper_sql_not_join_global_states(self):
        src = inspect.getsource(_batch_user_companion_states_with_db)
        self.assertIn("UserCompanionStateORM", src)
        self.assertNotIn("query(CompanionStateORM)", src)

    def test_other_scenes_overlay_user_states(self):
        src = inspect.getsource(CompanionManager._list_other_scenes)
        self.assertIn("_batch_user_companion_states_with_db", src)
        self.assertIn('us["turns"]', src)

    def test_to_dict_source_mentions_user_row_only(self):
        from services.companion_manager import Companion

        src = inspect.getsource(Companion.to_dict)
        self.assertIn("_counters_from_user_row", src)
        self.assertNotIn("query(CompanionStateORM)", src)

    def test_compat_fake_turns_removed(self):
        import api.companions as companions_api

        src = inspect.getsource(companions_api)
        self.assertNotIn("_compat_intimacy_record_turns", src)


if __name__ == "__main__":
    unittest.main()
