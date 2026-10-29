"""Feedback inbox：列表始终置顶客服；没聊过不显示 last_message_at。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from services.feedback import empty_inbox_payload, inbox_payload  # noqa: E402


class TestFeedbackInboxHasChat(unittest.TestCase):
    def test_empty_payload_still_pinned(self):
        out = empty_inbox_payload("en")
        self.assertTrue(out["pinned"])
        self.assertFalse(out["has_chat"])
        self.assertIsNone(out["thread_id"])
        self.assertIsNone(out["last_message_at"])
        self.assertEqual(out["unread_count"], 0)

    def test_no_thread_same_as_empty(self):
        out = inbox_payload(MagicMock(), None, lang="en")
        self.assertTrue(out["pinned"])
        self.assertFalse(out["has_chat"])
        self.assertIsNone(out["last_message_at"])

    def test_thread_without_messages_no_time(self):
        thread = SimpleNamespace(id=7, status="open", updated_at=None)
        with patch("services.feedback.last_message_row", return_value=None), patch(
            "services.feedback.user_unread_count", return_value=0
        ):
            out = inbox_payload(MagicMock(), thread, lang="en")
        self.assertTrue(out["pinned"])
        self.assertFalse(out["has_chat"])
        self.assertIsNone(out["last_message_at"])
        self.assertEqual(out["thread_id"], 7)

    def test_last_message_at_is_chat_time(self):
        from datetime import datetime, timezone

        ts = datetime(2026, 10, 15, 8, 1, tzinfo=timezone.utc)
        last = SimpleNamespace(content="hi", sender="user", created_at=ts)
        thread = SimpleNamespace(id=9, status="open", updated_at=ts)
        with patch("services.feedback.last_message_row", return_value=last), patch(
            "services.feedback.user_unread_count", return_value=0
        ):
            out = inbox_payload(MagicMock(), thread, lang="en")
        self.assertTrue(out["pinned"])
        self.assertTrue(out["has_chat"])
        self.assertIn("2026-10-15T08:01:00", out["last_message_at"] or "")
        self.assertEqual(out["last_message_sender"], "user")


if __name__ == "__main__":
    unittest.main()
