"""限时优惠会话：倒计时不重开、到期/已买不再展示。"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.limited_offer import remaining_sec, status_flags  # noqa: E402


class TestLimitedOfferSession(unittest.TestCase):
    def test_remaining_and_show(self):
        now = datetime.now(timezone.utc)
        row = SimpleNamespace(
            used_at=None,
            started_at=now,
            expires_at=now + timedelta(seconds=90),
            first_paywall_at=now,
            product_id=8,
        )
        self.assertTrue(80 <= remaining_sec(row, now) <= 90)
        flags = status_flags(row, paid=False)
        self.assertTrue(flags["show"])
        self.assertEqual(flags["product_id"], 8)

    def test_expired_hides(self):
        now = datetime.now(timezone.utc)
        row = SimpleNamespace(
            used_at=None,
            started_at=now - timedelta(seconds=10),
            expires_at=now - timedelta(seconds=1),
            first_paywall_at=now,
            product_id=8,
        )
        flags = status_flags(row, paid=False)
        self.assertFalse(flags["show"])
        self.assertEqual(flags["session_reason"], "expired")
        self.assertEqual(flags["remaining_sec"], 0)

    def test_paid_hides(self):
        now = datetime.now(timezone.utc)
        row = SimpleNamespace(
            used_at=now,
            started_at=now,
            expires_at=now + timedelta(seconds=90),
            first_paywall_at=now,
            product_id=8,
        )
        flags = status_flags(row, paid=True)
        self.assertFalse(flags["show"])
        self.assertEqual(flags["session_reason"], "already_paid")


if __name__ == "__main__":
    unittest.main()
