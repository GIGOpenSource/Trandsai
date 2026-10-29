"""Say Hi 触发智能体回复调度（含扣费退回）。"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import services.im_admin as im_admin
import services.im_reply_mode as im_reply_mode
from services import say_hi_reply as m


class TestSayHiBillingFields(unittest.TestCase):
    def test_free_turn_fields(self):
        out = m.billing_fields_from_charge_meta(
            {"use_free": True, "free_turns_remaining": 2, "credits_charged": 0}
        )
        self.assertTrue(out["use_free"])
        self.assertTrue(out["free_turn_used"])
        self.assertEqual(out["remaining"], 2)
        self.assertEqual(out["free_turns_remaining"], 2)

    def test_credits_fields(self):
        out = m.billing_fields_from_charge_meta(
            {"use_free": False, "credits_charged": 3}
        )
        self.assertFalse(out["use_free"])
        self.assertNotIn("free_turn_used", out)
        self.assertEqual(out["credits_charged"], 3)
        self.assertEqual(out["remaining"], 0)

    def test_charge_error_detail(self):
        body = m.normalize_charge_error_detail(
            {"reason": "no_free_turns_and_no_credits"}
        )
        self.assertTrue(body["need_vip"])
        self.assertEqual(body["remaining"], 0)

    def test_enrich_need_paywall_false_when_daily_zero_but_ok(self):
        with patch(
            "services.say_hi_quota.get_say_hi_daily_snapshot",
            return_value={
                "say_hi_daily_limit": 10,
                "say_hi_daily_used": 10,
                "say_hi_daily_remaining": 0,
                "say_hi_daily_exhausted": True,
            },
        ):
            out = m.enrich_say_hi_response(
                {"is_say_hi": True, "message": "hi"},
                user_id=1,
                charge_meta={
                    "use_say_hi_daily_free": False,
                    "say_hi_daily_remaining": 0,
                    "use_free": False,
                    "credits_charged": 3,
                },
                need_paywall=False,
            )
        self.assertEqual(out["say_hi_daily_remaining"], 0)
        self.assertFalse(out["need_paywall"])
        self.assertTrue(out["say_hi_daily_exhausted"])

    def test_enrich_need_confirm_when_daily_exhausted(self):
        with patch(
            "services.say_hi_quota.get_say_hi_daily_snapshot",
            return_value={
                "say_hi_daily_limit": 10,
                "say_hi_daily_used": 10,
                "say_hi_daily_remaining": 0,
                "say_hi_daily_exhausted": True,
            },
        ):
            out = m.enrich_say_hi_response(
                {
                    "is_say_hi": False,
                    "message": None,
                    "need_confirm_use_chat_free": True,
                    "say_hi_daily_exhausted": True,
                },
                user_id=1,
                charge_meta={
                    "use_say_hi_daily_free": False,
                    "say_hi_daily_remaining": 0,
                    "say_hi_daily_exhausted": True,
                    "need_confirm_use_chat_free": True,
                    "use_free": False,
                    "credits_charged": 0,
                },
            )
        self.assertTrue(out["say_hi_daily_exhausted"])
        self.assertTrue(out["need_confirm_use_chat_free"])
        self.assertFalse(out["need_paywall"])
        self.assertIsNone(out["message"])

    def test_enrich_need_paywall_true_when_blocked(self):
        with patch(
            "services.say_hi_quota.get_say_hi_daily_snapshot",
            return_value={
                "say_hi_daily_limit": 10,
                "say_hi_daily_used": 10,
                "say_hi_daily_remaining": 0,
                "say_hi_daily_exhausted": True,
            },
        ), patch.object(m, "current_free_turns_remaining", return_value=0):
            out = m.say_hi_quota_blocked_response(
                user_id=1,
                companion_id="abcd1234",
                charge_detail={"reason": "no_free_turns_and_no_credits", "need_vip": True},
            )
        self.assertTrue(out["need_paywall"])
        self.assertTrue(out["quota_blocked"])
        self.assertIsNone(out["message"])
        self.assertEqual(out["say_hi_daily_remaining"], 0)

class TestSayHiReply(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_creates_task(self):
        with (
            patch.object(m, "_run_say_hi_assistant_reply", new_callable=AsyncMock) as run,
            patch.object(im_admin, "ensure_user_companion_state"),
        ):
            m.schedule_say_hi_assistant_reply(
                user_id=1,
                companion_id="abcd1234",
                user_text="Hi there",
                charge_meta={"use_free": True},
            )
            await asyncio.sleep(0)
            run.assert_awaited()

    async def test_taken_over_keeps_charge(self):
        with (
            patch.object(
                im_admin, "notify_ops_new_user_message", new_callable=AsyncMock
            ),
            patch.object(im_admin, "is_session_taken_over", return_value=True),
            patch.object(
                im_admin, "broadcast_admin_im", new_callable=AsyncMock
            ) as broadcast,
            patch.object(m, "_refund") as refund,
            patch.object(m, "_deliver_charged_reply", new_callable=AsyncMock) as deliver,
        ):
            await m._run_say_hi_assistant_reply(
                2, "abcd1234", "hello", charge_meta={"use_free": True}
            )
            broadcast.assert_awaited()
            deliver.assert_not_awaited()
            refund.assert_not_called()

    async def test_deliver_fail_refunds(self):
        with (
            patch.object(
                im_admin, "notify_ops_new_user_message", new_callable=AsyncMock
            ),
            patch.object(im_admin, "is_session_taken_over", return_value=False),
            patch.object(im_reply_mode, "try_defer_human_first", return_value=False),
            patch.object(
                m, "_deliver_charged_reply", new_callable=AsyncMock, return_value=False
            ),
            patch.object(m, "_refund") as refund,
        ):
            meta = {"use_free": True}
            await m._run_say_hi_assistant_reply(3, "abcd1234", "hello", charge_meta=meta)
            refund.assert_called_once_with(3, meta)

    async def test_deliver_ok_no_refund(self):
        with (
            patch.object(
                im_admin, "notify_ops_new_user_message", new_callable=AsyncMock
            ),
            patch.object(im_admin, "is_session_taken_over", return_value=False),
            patch.object(im_reply_mode, "try_defer_human_first", return_value=False),
            patch.object(
                m, "_deliver_charged_reply", new_callable=AsyncMock, return_value=True
            ),
            patch.object(m, "_refund") as refund,
        ):
            await m._run_say_hi_assistant_reply(
                4, "abcd1234", "hello", charge_meta={"credits_charged": 3}
            )
            refund.assert_not_called()


if __name__ == "__main__":
    unittest.main()
