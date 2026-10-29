"""refund_chat_turn：无 meta / 空 meta 必须是空操作。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestRefundChatTurn(unittest.TestCase):
    def test_noop_without_meta(self):
        try:
            from services.chat_turn import refund_chat_turn
        except Exception:
            self.skipTest("chat_turn dependencies not installed")
        refund_chat_turn(1, None)
        refund_chat_turn(1, {})

    def test_restore_free_turn_path(self):
        try:
            from services import chat_turn as ct
        except Exception:
            self.skipTest("chat_turn dependencies not installed")

        calls = []

        class _Db:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def commit(self):
                calls.append("commit")

        with patch.object(ct, "get_db", return_value=_Db()):
            with patch(
                "services.credit_service.restore_free_turn",
                lambda db, uid, companion_id=None: calls.append(
                    ("free", uid, companion_id)
                ),
            ):
                ct.refund_chat_turn(
                    9, {"use_free": True, "companion_id": "ab12cd34"}
                )
        self.assertIn("commit", calls)
        self.assertIn(("free", 9, "ab12cd34"), calls)


if __name__ == "__main__":
    unittest.main()
