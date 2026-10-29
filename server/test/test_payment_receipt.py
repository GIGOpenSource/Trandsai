"""支付收据解析单测：不打 Apple/Google 网络。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.payment_receipt import (
    apple_app_account_token_for_user_id,
    extract_google_obfuscated_account_id,
    interpret_apple_verify_result,
    interpret_google_purchase_payload,
    interpret_google_subscription_v2,
    normalize_apple_uuid,
    parse_apple_account_user_id,
    parse_google_account_user_id,
)


class TestAppleAppAccountToken(unittest.TestCase):
    def test_roundtrip_user_id(self):
        token = apple_app_account_token_for_user_id(73)
        self.assertEqual(token, "00000000-0000-0000-0000-000000000049")
        self.assertEqual(parse_apple_account_user_id(token), 73)

    def test_random_uuid_ignored_as_int_user(self):
        self.assertIsNone(
            parse_apple_account_user_id("550e8400-e29b-41d4-a716-446655440000")
        )

    def test_normalize_uuid(self):
        self.assertEqual(
            normalize_apple_uuid("550E8400-E29B-41D4-A716-446655440000"),
            "550e8400-e29b-41d4-a716-446655440000",
        )
        self.assertEqual(normalize_apple_uuid("not-a-uuid"), "")


class TestAppleReceiptInterpret(unittest.TestCase):
    def test_empty_result(self):
        self.assertFalse(interpret_apple_verify_result(None)["valid"])
        self.assertEqual(
            interpret_apple_verify_result(None)["reason"], "verify_request_failed"
        )

    def test_sandbox_status_not_valid_until_retried(self):
        out = interpret_apple_verify_result({"status": 21007})
        self.assertFalse(out["valid"])
        self.assertEqual(out["reason"], "apple_status_21007")

    def test_ok_latest_transaction(self):
        out = interpret_apple_verify_result(
            {
                "status": 0,
                "environment": "Sandbox",
                "latest_receipt_info": [
                    {"transaction_id": "1", "product_id": "old"},
                    {
                        "transaction_id": "tx-new",
                        "original_transaction_id": "tx-orig",
                        "product_id": "com.trandsai.vip.weekly",
                        "purchase_date_ms": "1",
                        "expires_date_ms": "2",
                        "is_trial_period": "false",
                    },
                ],
                "pending_renewal_info": [{"auto_renew_status": "1"}],
            }
        )
        self.assertTrue(out["valid"])
        self.assertEqual(out["transaction_id"], "tx-new")
        self.assertEqual(out["product_id"], "com.trandsai.vip.weekly")
        self.assertTrue(out["auto_renew_status"])
        self.assertEqual(out["environment"], "Sandbox")


class TestGooglePurchaseInterpret(unittest.TestCase):
    def test_purchased(self):
        out = interpret_google_purchase_payload(
            {
                "purchaseState": 0,
                "orderId": "GPA.1",
                "purchaseTimeMillis": 9,
                "obfuscatedExternalAccountId": "42",
            },
            "pack_250",
        )
        self.assertTrue(out["valid"])
        self.assertEqual(out["order_id"], "GPA.1")
        self.assertEqual(out["product_id"], "pack_250")
        self.assertEqual(out["obfuscated_external_account_id"], "42")
        self.assertEqual(parse_google_account_user_id(out["obfuscated_external_account_id"]), 42)

    def test_cancelled(self):
        out = interpret_google_purchase_payload({"purchaseState": 1}, "pack_250")
        self.assertFalse(out["valid"])


class TestGoogleSubscriptionV2Interpret(unittest.TestCase):
    def test_trial_active(self):
        out = interpret_google_subscription_v2(
            {
                "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
                "acknowledgementState": "ACKNOWLEDGEMENT_STATE_PENDING",
                "latestOrderId": "GPA.1",
                "startTime": "2026-10-20T01:00:00Z",
                "testPurchase": {},
                "externalAccountIdentifiers": {"obfuscatedExternalAccountId": "99"},
                "lineItems": [
                    {
                        "productId": "vip_monthly",
                        "expiryTime": "2026-10-23T01:00:00Z",
                        "autoRenewingPlan": {"autoRenewEnabled": True},
                        "offerDetails": {"basePlanId": "monthly", "offerId": "intro"},
                        "offerPhase": {"freeTrial": {}},
                    }
                ],
            }
        )
        self.assertTrue(out["valid"])
        self.assertTrue(out["is_trial_period"])
        self.assertTrue(out["needs_ack"])
        self.assertEqual(out["product_id"], "vip_monthly")
        self.assertEqual(out["expiry_time"], "2026-10-23T01:00:00Z")
        self.assertTrue(out["expiry_time_millis"])
        self.assertTrue(out["is_test_purchase"])
        self.assertEqual(out["obfuscated_external_account_id"], "99")

    def test_obfuscated_from_nested_raw(self):
        aid = extract_google_obfuscated_account_id(
            {"raw": {"obfuscatedExternalAccountId": "7"}}
        )
        self.assertEqual(aid, "7")
        self.assertIsNone(parse_google_account_user_id("not-a-user"))

    def test_pending_not_valid(self):
        out = interpret_google_subscription_v2(
            {"subscriptionState": "SUBSCRIPTION_STATE_PENDING", "lineItems": []}
        )
        self.assertFalse(out["valid"])
        self.assertEqual(out["reason"], "subscription_pending")


if __name__ == "__main__":
    unittest.main()
