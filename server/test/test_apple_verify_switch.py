"""Apple 21007 切沙箱：mock HTTP，不打商店网络。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from services.payment_verify import (
        APPLE_VERIFY_URL_PROD,
        APPLE_VERIFY_URL_SANDBOX,
        verify_apple_receipt,
    )
except ImportError:  # pragma: no cover
    verify_apple_receipt = None  # type: ignore
    APPLE_VERIFY_URL_PROD = ""
    APPLE_VERIFY_URL_SANDBOX = ""


@unittest.skipUnless(verify_apple_receipt, "requests / payment_verify unavailable")
class TestAppleVerifySwitch(unittest.TestCase):
    def test_empty_receipt_skips_http(self):
        with patch("services.payment_verify._do_apple_verify") as mock_http:
            out = verify_apple_receipt("  ")
        mock_http.assert_not_called()
        self.assertFalse(out["valid"])
        self.assertEqual(out["reason"], "empty_receipt")

    def test_21007_retries_sandbox(self):
        calls = []

        def fake(_payload, url):
            calls.append(url)
            if url == APPLE_VERIFY_URL_PROD:
                return {"status": 21007}
            return {
                "status": 0,
                "environment": "Sandbox",
                "latest_receipt_info": [
                    {"transaction_id": "t1", "product_id": "p1"},
                ],
            }

        with patch("services.payment_verify._do_apple_verify", side_effect=fake):
            out = verify_apple_receipt("BASE64RECEIPT")
        self.assertTrue(out["valid"])
        self.assertEqual(calls[0], APPLE_VERIFY_URL_PROD)
        self.assertEqual(calls[1], APPLE_VERIFY_URL_SANDBOX)
        self.assertEqual(out["transaction_id"], "t1")

    def test_google_missing_token_skips_http(self):
        from services.payment_verify import verify_google_purchase

        with patch("services.payment_verify._get_google_access_token") as mock_tok:
            out = verify_google_purchase("pack_250", "  ")
        mock_tok.assert_not_called()
        self.assertFalse(out["valid"])
        self.assertEqual(out["reason"], "missing_product_or_token")


if __name__ == "__main__":
    unittest.main()
