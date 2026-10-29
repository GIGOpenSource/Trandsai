"""VIP daily gift refresh + missed-day catch-up on login."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import services.credit_service as credit_service  # noqa: E402


class TestRefreshDailyGifts(unittest.TestCase):
    def _uc(self, **kwargs):
        uc = MagicMock()
        uc.vip_type = "weekly"
        uc.vip_expire_at = datetime.now(timezone.utc) + timedelta(days=5)
        uc.daily_gift_total = 20
        uc.daily_gift_remaining = 0
        uc.balance = 0
        uc.max_agents = 1
        uc.last_daily_gift_date = ""
        for k, v in kwargs.items():
            setattr(uc, k, v)
        return uc

    @patch.object(credit_service, "_write_vip_gift_recharge")
    @patch.object(credit_service, "_write_transaction")
    @patch.object(credit_service, "get_or_create_user_credits")
    def test_grants_today_when_last_was_yesterday(self, mock_get, mock_tx, mock_rr):
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).strftime("%Y-%m-%d")
        uc = self._uc(last_daily_gift_date=yesterday, daily_gift_remaining=0)
        mock_get.return_value = uc

        credit_service.refresh_daily_gifts(MagicMock(), 1)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(uc.daily_gift_remaining, 20)
        self.assertEqual(uc.last_daily_gift_date, today)
        self.assertEqual(uc.balance, 0)
        self.assertTrue(mock_tx.called)

    @patch.object(credit_service, "_write_vip_gift_recharge")
    @patch.object(credit_service, "_write_transaction")
    @patch.object(credit_service, "get_or_create_user_credits")
    def test_catchup_missed_days_into_balance(self, mock_get, mock_tx, mock_rr):
        three_days_ago = (datetime.now(timezone.utc).date() - timedelta(days=3)).strftime("%Y-%m-%d")
        uc = self._uc(last_daily_gift_date=three_days_ago)
        mock_get.return_value = uc

        credit_service.refresh_daily_gifts(MagicMock(), 42)

        self.assertEqual(uc.balance, 40)
        self.assertEqual(uc.daily_gift_remaining, 20)
        self.assertEqual(mock_tx.call_count, 2)

    @patch.object(credit_service, "_write_transaction")
    @patch.object(credit_service, "get_or_create_user_credits")
    def test_idempotent_same_day(self, mock_get, mock_tx):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        uc = self._uc(last_daily_gift_date=today, daily_gift_remaining=7)
        mock_get.return_value = uc

        credit_service.refresh_daily_gifts(MagicMock(), 1)

        self.assertEqual(uc.daily_gift_remaining, 7)
        mock_tx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
