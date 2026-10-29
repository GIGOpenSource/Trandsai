"""Say Hi 每日免费额度 + 扣费字段。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from services import say_hi_quota as q
from services import say_hi_reply as m


class TestSayHiQuotaNormalize(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(q.clamp_daily_limit(10), 10)
        self.assertEqual(q.clamp_daily_limit(-1), 0)
        self.assertEqual(q.clamp_daily_limit(99999), 9999)

    def test_normalize_json(self):
        out = q.normalize_say_hi_daily_config_json({"say_hi_daily_free_limit": "12"})
        self.assertEqual(out["say_hi_daily_free_limit"], 12)


class TestSayHiBillingFields(unittest.TestCase):
    def test_daily_free_fields(self):
        out = m.billing_fields_from_charge_meta(
            {
                "use_say_hi_daily_free": True,
                "say_hi_daily_remaining": 9,
                "say_hi_daily_limit": 10,
                "use_free": False,
                "credits_charged": 0,
            }
        )
        self.assertTrue(out["use_say_hi_daily_free"])
        self.assertTrue(out["say_hi_daily_free_used"])
        self.assertEqual(out["say_hi_daily_remaining"], 9)
        self.assertFalse(out["use_free"])

    def test_refund_daily(self):
        with patch("services.say_hi_quota.restore_say_hi_daily_free") as restore:
            m._refund(1, {"use_say_hi_daily_free": True})
            restore.assert_called_once_with(1)

    def test_refund_chat(self):
        try:
            import services.chat_turn  # noqa: F401
        except Exception:
            self.skipTest("chat_turn unavailable")
        with patch("services.chat_turn.refund_chat_turn") as refund:
            m._refund(2, {"use_free": True, "free_turns_remaining": 1})
            refund.assert_called_once()


class TestConsumeDaily(unittest.TestCase):
    def test_consume_none_when_limit_zero(self):
        with patch.object(q, "get_say_hi_daily_free_limit", return_value=0):
            self.assertIsNone(q.try_consume_say_hi_daily_free(99))

    def test_consume_ok(self):
        uc = MagicMock()
        uc.say_hi_daily_date = ""
        uc.say_hi_daily_used = 0

        class _Db:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def commit(self):
                pass

        with (
            patch.object(q, "get_say_hi_daily_free_limit", return_value=10),
            patch.object(q, "_utc_today", return_value="2026-09-23"),
            patch.object(q, "get_db", return_value=_Db()),
            patch(
                "services.credit_service.get_or_create_user_credits",
                return_value=uc,
            ),
        ):
            meta = q.try_consume_say_hi_daily_free(7)
        self.assertIsNotNone(meta)
        self.assertTrue(meta["use_say_hi_daily_free"])
        self.assertEqual(meta["say_hi_daily_remaining"], 9)
        self.assertEqual(uc.say_hi_daily_used, 1)

    def test_consume_exhausted(self):
        uc = MagicMock()
        uc.say_hi_daily_date = "2026-09-23"
        uc.say_hi_daily_used = 10

        class _Db:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def commit(self):
                pass

        with (
            patch.object(q, "get_say_hi_daily_free_limit", return_value=10),
            patch.object(q, "_utc_today", return_value="2026-09-23"),
            patch.object(q, "get_db", return_value=_Db()),
            patch(
                "services.credit_service.get_or_create_user_credits",
                return_value=uc,
            ),
        ):
            self.assertIsNone(q.try_consume_say_hi_daily_free(7))


class TestSayHiNoAutoChatFree(unittest.TestCase):
    def test_exhausted_without_confirm_does_not_charge_chat(self):
        with (
            patch.object(q, "try_consume_say_hi_daily_free", return_value=None),
            patch.object(
                q,
                "get_say_hi_daily_snapshot",
                return_value={
                    "say_hi_daily_limit": 10,
                    "say_hi_daily_used": 10,
                    "say_hi_daily_remaining": 0,
                    "say_hi_daily_exhausted": True,
                },
            ),
            patch.object(q, "_charge_chat_turn") as charge,
        ):
            out = q.decide_say_hi_charge(1, "abcd1234", use_chat_free=False)
        charge.assert_not_called()
        self.assertTrue(out["need_confirm"])
        self.assertTrue(out["charge_meta"]["need_confirm_use_chat_free"])
        self.assertFalse(out.get("ok"))

    def test_exhausted_with_confirm_charges_chat(self):
        with (
            patch.object(q, "try_consume_say_hi_daily_free", return_value=None),
            patch.object(
                q,
                "get_say_hi_daily_snapshot",
                return_value={
                    "say_hi_daily_limit": 10,
                    "say_hi_daily_used": 10,
                    "say_hi_daily_remaining": 0,
                    "say_hi_daily_exhausted": True,
                },
            ),
            patch.object(
                q,
                "_charge_chat_turn",
                return_value={"use_free": True, "free_turns_used": 1, "free_turns_limit": 3},
            ) as charge,
        ):
            out = q.decide_say_hi_charge(1, "abcd1234", use_chat_free=True)
        charge.assert_called_once()
        self.assertTrue(out["ok"])
        self.assertTrue(out["charge_meta"]["say_hi_daily_exhausted"])
        self.assertTrue(out["charge_meta"]["use_free"])


class TestChatFreeTurnsStatus(unittest.TestCase):
    def test_remaining_from_account_snapshot(self):
        from unittest.mock import MagicMock, patch
        from services.credit_service import get_chat_free_turns_status

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = db
        ctx.__exit__.return_value = False
        snap = {
            "free_turns_limit": 5,
            "free_turns_used": 3,
            "free_turns_remaining": 2,
        }
        say = {
            "say_hi_daily_remaining": 7,
            "say_hi_daily_exhausted": False,
            "say_hi_daily_limit": 10,
        }
        with patch("services.credit_service.get_db", return_value=ctx), patch(
            "services.credit_service.get_user_free_turns_snapshot",
            return_value=snap,
        ), patch("services.credit_service.is_vip_active", return_value=False), patch(
            "services.say_hi_quota.get_say_hi_daily_snapshot",
            return_value=say,
        ):
            out = get_chat_free_turns_status(9, "ab12cd34")
        self.assertEqual(out["remaining"], 2)
        self.assertEqual(out["free_turns_used"], 3)
        self.assertEqual(out["free_turns_limit"], 5)
        self.assertEqual(out["companion_id"], "ab12cd34")
        self.assertFalse(out["is_vip"])
        self.assertEqual(out["say_hi_daily_remaining"], 7)


class TestFreeTurnUsedPayload(unittest.TestCase):
    def test_report_only_when_flag_true(self):
        from services.credit_service import free_turn_used_payload

        snap = {
            "free_turns_limit": 3,
            "free_turns_used": 3,
            "free_turns_remaining": 0,
        }
        enter = free_turn_used_payload(snap, report_free_quota_reached=False)
        self.assertFalse(enter["report_free_quota_reached"])
        self.assertEqual(enter["free_turns_used"], 3)
        hit = free_turn_used_payload(snap, report_free_quota_reached=True)
        self.assertTrue(hit["report_free_quota_reached"])


if __name__ == "__main__":
    unittest.main()
