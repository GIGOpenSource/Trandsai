"""金币充值：优先使用福利商品 credits_amount。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestResolveRechargeCredits(unittest.TestCase):
    @patch("services.credit_service._load_vip_config")
    @patch("services.credit_service._lookup_point_product")
    def test_prefers_benefit_product_over_config(self, lookup, cfg):
        from services.credit_service import _resolve_recharge_credits

        product = MagicMock(credits_amount=888, app_key="trandsai")
        lookup.return_value = product
        cfg.return_value = {"recharge_packages": {"pack_250": {"credits": 250}}}
        amount, got = _resolve_recharge_credits(MagicMock(), "pack_250", credits_hint=250)
        self.assertEqual(amount, 888)
        self.assertIs(got, product)
        cfg.assert_not_called()

    @patch("services.credit_service._load_vip_config")
    @patch("services.credit_service._lookup_point_product")
    def test_uses_pending_hint_when_product_credits_zero(self, lookup, cfg):
        from services.credit_service import _resolve_recharge_credits

        lookup.return_value = MagicMock(credits_amount=0)
        amount, _ = _resolve_recharge_credits(MagicMock(), "gold_sku", credits_hint=120)
        self.assertEqual(amount, 120)
        cfg.assert_not_called()

    @patch("services.credit_service._load_vip_config")
    @patch("services.credit_service._lookup_point_product")
    def test_falls_back_to_config(self, lookup, cfg):
        from services.credit_service import _resolve_recharge_credits

        lookup.return_value = None
        cfg.return_value = {"recharge_packages": {"pack_250": {"credits": 250}}}
        amount, _ = _resolve_recharge_credits(MagicMock(), "pack_250")
        self.assertEqual(amount, 250)

    @patch("services.credit_service._write_transaction")
    @patch("services.credit_service.get_or_create_user_credits")
    @patch("services.credit_service._resolve_recharge_credits")
    def test_recharge_adds_balance(self, resolve, mock_uc, mock_tx):
        from services.credit_service import recharge_credits

        resolve.return_value = (500, None)
        uc = MagicMock()
        uc.balance = 10
        uc.daily_gift_remaining = 0
        uc.vip_type = ""
        mock_uc.return_value = uc
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = recharge_credits(db, 7, "gold_sku", "google_play", "tok1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["credits_added"], 500)
        self.assertEqual(uc.balance, 510)
        db.add.assert_called()


if __name__ == "__main__":
    unittest.main()
