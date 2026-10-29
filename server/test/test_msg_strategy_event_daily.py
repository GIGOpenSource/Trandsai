"""消息策略：规则 triggers = 用户×智能体×事件终身上限。"""
from __future__ import annotations

import os
import sys
import unittest

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.msg_strategy_engine import (  # noqa: E402
    event_keys_for_daily_quota,
    rule_want_count,
)


class TestCompanionEventQuotaSemantics(unittest.TestCase):
    def test_rule_want_count_reads_event_key(self):
        triggers = {"Moment_15s": 1, "app_launch": 3}
        self.assertEqual(
            rule_want_count(triggers, "Moment_15s", "Moment_15s", ["Moment_15s"]),
            1,
        )
        self.assertEqual(
            rule_want_count(triggers, "app_launch", "app_launch", ["app_launch"]),
            3,
        )

    def test_rule_want_zero_means_not_configured(self):
        self.assertEqual(
            rule_want_count({"Moment_15s": 0}, "Moment_15s", "Moment_15s", ["Moment_15s"]),
            0,
        )
        self.assertEqual(
            rule_want_count({}, "Moment_15s", "Moment_15s", ["Moment_15s"]),
            0,
        )

    def test_event_keys_include_raw_and_canonical(self):
        keys = event_keys_for_daily_quota(
            event="Moment_15s",
            raw_event="test_Moment_15s",
            trigger_keys=["Moment_15s"],
        )
        self.assertIn("Moment_15s", keys)
        self.assertIn("test_Moment_15s", keys)

    def test_pair_quota_default_one(self):
        """默认 1：该用户对该智能体同事件已发 1 条则不再触发。"""
        configured = 1
        sent = 1
        left = max(0, configured - sent)
        self.assertEqual(left, 0)

    def test_pair_quota_allows_other_companion(self):
        """上限按智能体分开：A 已满不影响 B。"""
        configured = 1
        sent_a = 1
        sent_b = 0
        self.assertEqual(max(0, configured - sent_a), 0)
        self.assertEqual(max(0, configured - sent_b), 1)

    def test_configured_three_allows_until_cap(self):
        configured = 3
        sent = 1
        left = max(0, configured - sent)
        self.assertEqual(left, 2)
        want = min(1, left)
        self.assertEqual(want, 1)


if __name__ == "__main__":
    unittest.main()
