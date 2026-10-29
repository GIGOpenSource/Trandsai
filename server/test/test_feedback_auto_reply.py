"""Feedback auto-reply round rules (no DB)."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.feedback import AUTO_REPLY_DELAY_S, thread_due_for_auto_reply


class _Db:
    def __init__(self, official_after=False):
        self.official_after = official_after

    def query(self, *_a, **_k):
        return self

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return (1,) if self.official_after else None


class TestFeedbackAutoReplyDue(unittest.TestCase):
    def test_not_due_without_user_message(self):
        t = SimpleNamespace(last_user_msg_at=None, auto_reply_after_user_at=None, id=1)
        self.assertFalse(thread_due_for_auto_reply(_Db(), t))

    def test_due_after_delay(self):
        now = datetime.now(timezone.utc)
        t = SimpleNamespace(
            id=1,
            last_user_msg_at=now - timedelta(seconds=AUTO_REPLY_DELAY_S + 1),
            auto_reply_after_user_at=None,
        )
        self.assertTrue(thread_due_for_auto_reply(_Db(), t, now=now))

    def test_reset_timer_not_due_yet(self):
        now = datetime.now(timezone.utc)
        t = SimpleNamespace(
            id=1,
            last_user_msg_at=now - timedelta(seconds=5),
            auto_reply_after_user_at=None,
        )
        self.assertFalse(thread_due_for_auto_reply(_Db(), t, now=now))

    def test_once_per_round(self):
        now = datetime.now(timezone.utc)
        last = now - timedelta(seconds=AUTO_REPLY_DELAY_S + 5)
        t = SimpleNamespace(id=1, last_user_msg_at=last, auto_reply_after_user_at=last)
        self.assertFalse(thread_due_for_auto_reply(_Db(), t, now=now))

    def test_new_round_after_user_sends_again(self):
        now = datetime.now(timezone.utc)
        prev = now - timedelta(seconds=120)
        last = now - timedelta(seconds=AUTO_REPLY_DELAY_S + 1)
        t = SimpleNamespace(id=1, last_user_msg_at=last, auto_reply_after_user_at=prev)
        self.assertTrue(thread_due_for_auto_reply(_Db(), t, now=now))

    def test_skip_if_admin_already_replied(self):
        now = datetime.now(timezone.utc)
        t = SimpleNamespace(
            id=1,
            last_user_msg_at=now - timedelta(seconds=AUTO_REPLY_DELAY_S + 1),
            auto_reply_after_user_at=None,
        )
        self.assertFalse(thread_due_for_auto_reply(_Db(official_after=True), t, now=now))

    def test_auto_send_disabled(self):
        from services.feedback import ensure_welcome, fire_auto_reply, fire_due_auto_replies

        self.assertIsNone(ensure_welcome(None, None))
        self.assertIsNone(fire_auto_reply(None, None))
        self.assertEqual(fire_due_auto_replies(None), 0)


if __name__ == "__main__":
    unittest.main()
