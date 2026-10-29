"""IM 回复模式：优先人工 / AI。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.im_reply_mode import (
    REPLY_MODE_AI,
    REPLY_MODE_HUMAN_FIRST,
    clamp_wait_seconds,
    normalize_reply_mode,
    should_defer_auto_reply,
)


class TestNormalize(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(normalize_reply_mode("ai"), REPLY_MODE_AI)
        self.assertEqual(normalize_reply_mode("human_first"), REPLY_MODE_HUMAN_FIRST)
        self.assertEqual(normalize_reply_mode("prefer_human"), REPLY_MODE_HUMAN_FIRST)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            normalize_reply_mode("robot")

    def test_wait_clamp(self):
        self.assertEqual(clamp_wait_seconds(30), 30)
        self.assertEqual(clamp_wait_seconds(1), 5)
        self.assertEqual(clamp_wait_seconds(9999), 600)


class TestShouldDefer(unittest.TestCase):
    def test_ai_mode_not_defer(self):
        with mock.patch(
            "services.im_reply_mode.get_or_create_settings",
            return_value={"reply_mode": "ai", "wait_seconds": 30},
        ):
            defer, wait = should_defer_auto_reply(1, "abc12345")
        self.assertFalse(defer)
        self.assertEqual(wait, 0)

    def test_human_first_without_ops_not_defer(self):
        with mock.patch(
            "services.im_reply_mode.get_or_create_settings",
            return_value={"reply_mode": "human_first", "wait_seconds": 45},
        ):
            with mock.patch(
                "services.im_reply_mode._is_session_taken_over", return_value=False
            ):
                with mock.patch(
                    "services.im_reply_mode._companion_has_ops_admin",
                    return_value=False,
                ):
                    defer, wait = should_defer_auto_reply(1, "abc12345")
        self.assertFalse(defer)
        self.assertEqual(wait, 0)

    def test_human_first_with_ops_defers(self):
        with mock.patch(
            "services.im_reply_mode.get_or_create_settings",
            return_value={"reply_mode": "human_first", "wait_seconds": 45},
        ):
            with mock.patch(
                "services.im_reply_mode._is_session_taken_over", return_value=False
            ):
                with mock.patch(
                    "services.im_reply_mode._companion_has_ops_admin",
                    return_value=True,
                ):
                    defer, wait = should_defer_auto_reply(1, "abc12345")
        self.assertTrue(defer)
        self.assertEqual(wait, 45)

    def test_taken_over_not_defer(self):
        with mock.patch(
            "services.im_reply_mode.get_or_create_settings",
            return_value={"reply_mode": "human_first", "wait_seconds": 45},
        ):
            with mock.patch(
                "services.im_reply_mode._is_session_taken_over", return_value=True
            ):
                defer, wait = should_defer_auto_reply(1, "abc12345")
        self.assertFalse(defer)


if __name__ == "__main__":
    unittest.main()
