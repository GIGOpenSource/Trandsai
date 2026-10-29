"""VIP activate: stack expiry on renew, max_agents takes the larger quota."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestActivateVipHelpers(unittest.TestCase):
    def test_stack_and_rank(self):
        from services.credit_service import _pick_vip_plan_type, _stack_vip_expire

        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        cur = datetime(2026, 8, 26, tzinfo=timezone.utc)
        self.assertEqual(
            _stack_vip_expire(cur, now, 365),
            datetime(2027, 8, 26, tzinfo=timezone.utc),
        )
        self.assertEqual(_pick_vip_plan_type("weekly", "yearly"), "yearly")
        self.assertEqual(_pick_vip_plan_type("yearly", "weekly"), "yearly")

    @patch("services.credit_service._write_transaction")
    @patch("services.credit_service.get_or_create_user_credits")
    @patch("services.credit_service._resolve_vip_product_config")
    def test_new_purchase_stacks_and_takes_max_agents(self, mock_cfg, mock_uc, mock_tx):
        from services.credit_service import activate_vip

        mock_cfg.return_value = (25, 3, 365, None)
        uc = MagicMock()
        uc.vip_type = "weekly"
        uc.vip_expire_at = datetime(2026, 8, 26, tzinfo=timezone.utc)
        uc.vip_started_at = datetime(2026, 8, 19, tzinfo=timezone.utc)
        uc.max_agents = 1
        uc.balance = 0
        uc.daily_gift_remaining = 0
        mock_uc.return_value = uc

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = activate_vip(
            db, 1, "yearly", "google_play", "tx_new_year",
            product_id="vip_yearly",
            purchased_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(uc.vip_type, "yearly")
        self.assertEqual(uc.max_agents, 3)
        self.assertEqual(uc.vip_expire_at, datetime(2027, 8, 26, tzinfo=timezone.utc))
        db.add.assert_called()

    @patch("services.credit_service.get_or_create_user_credits")
    @patch("services.credit_service._resolve_vip_product_config")
    def test_same_token_upgrade_extends_expire(self, mock_cfg, mock_uc):
        from services.credit_service import activate_vip

        mock_cfg.return_value = (25, 3, 365, None)
        uc = MagicMock()
        uc.vip_type = "weekly"
        uc.vip_expire_at = datetime(2026, 8, 26, tzinfo=timezone.utc)
        uc.vip_started_at = datetime(2026, 8, 19, tzinfo=timezone.utc)
        uc.max_agents = 1
        mock_uc.return_value = uc

        existing = MagicMock()
        existing.id = 9
        existing.plan_type = "weekly"
        existing.expire_at = uc.vip_expire_at
        existing.max_agents = 1
        existing.daily_credits = 20

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = existing

        result = activate_vip(
            db, 1, "yearly", "google_play", "same_token",
            product_id="vip_yearly",
            purchased_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(result.get("reason"), "upgraded")
        self.assertEqual(existing.plan_type, "yearly")
        self.assertEqual(uc.max_agents, 3)
        self.assertEqual(uc.vip_expire_at, datetime(2027, 8, 26, tzinfo=timezone.utc))

    @patch("services.credit_service.get_or_create_user_credits")
    @patch("services.credit_service._resolve_vip_product_config")
    def test_same_token_repeat_does_not_restack(self, mock_cfg, mock_uc):
        from services.credit_service import activate_vip

        mock_cfg.return_value = (25, 3, 365, None)
        expire = datetime(2027, 8, 26, tzinfo=timezone.utc)
        uc = MagicMock()
        uc.vip_type = "yearly"
        uc.vip_expire_at = expire
        uc.max_agents = 3
        mock_uc.return_value = uc

        existing = MagicMock()
        existing.id = 9
        existing.plan_type = "yearly"
        existing.expire_at = expire
        existing.max_agents = 3

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = existing

        result = activate_vip(
            db, 1, "yearly", "google_play", "same_token",
            purchased_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )
        self.assertEqual(result.get("reason"), "already_processed")
        self.assertEqual(uc.vip_expire_at, expire)
        self.assertEqual(uc.max_agents, 3)


if __name__ == "__main__":
    unittest.main()
