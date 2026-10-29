"""超额用户消息落库：不进模型、相册；付费墙带 accepted。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from services.chat_turn import ChatTurnError, need_vip_ws_payload, unpaid_http_detail
    from services.memory import (
        UNPAID_SEND_STATUS,
        CompanionMemory,
        ShortTermMemory,
        is_unpaid_message,
        llm_visible_messages,
    )
    from services.strategy_image import annotate_chat_message
except Exception as e:  # pragma: no cover
    ChatTurnError = None  # type: ignore
    _IMPORT_ERR = e
else:
    _IMPORT_ERR = None


class TestUnpaidChat(unittest.TestCase):
    def setUp(self):
        if _IMPORT_ERR is not None:
            self.skipTest(f"deps missing: {_IMPORT_ERR}")

    def test_need_vip_payload_accepted(self):
        exc = ChatTurnError(403, {"reason": "no_free_turns_and_no_credits", "need_vip": True})
        msgs = [{"role": "user", "content": "hi", "send_status": "unpaid"}]
        body = need_vip_ws_payload(exc, msgs)
        self.assertEqual(body["type"], "need_vip")
        self.assertTrue(body["accepted"])
        self.assertEqual(body["send_status"], "unpaid")
        self.assertEqual(body["messages"], msgs)
        self.assertEqual(body["reason"], "no_free_turns_and_no_credits")

    def test_need_vip_402_balance(self):
        exc = ChatTurnError(402, {"balance": 3})
        body = need_vip_ws_payload(exc, [])
        self.assertEqual(body["reason"], "credits_insufficient")
        self.assertEqual(body["balance"], 3)
        self.assertTrue(body["accepted"])

    def test_http_detail(self):
        exc = ChatTurnError(403, {"reason": "no_free_turns_and_no_credits"})
        d = unpaid_http_detail(exc, [{"content": "x"}])
        self.assertTrue(d["accepted"])
        self.assertEqual(d["send_status"], UNPAID_SEND_STATUS)

    def test_llm_skips_unpaid(self):
        rows = [
            {"role": "user", "content": "ok", "send_status": ""},
            {"role": "user", "content": "blocked", "send_status": "unpaid"},
            {"role": "assistant", "content": "hey"},
        ]
        vis = llm_visible_messages(rows)
        self.assertEqual(len(vis), 2)
        self.assertFalse(any(is_unpaid_message(m) for m in vis))

    def test_recent_turns_skip_unpaid(self):
        mem = ShortTermMemory("abcd1234", user_id=1)
        mem.get_recent = MagicMock(
            return_value=[
                {"role": "user", "content": "a", "send_status": ""},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c", "send_status": "unpaid"},
            ]
        )
        turns = mem.get_recent_turns(20)
        texts = [t["content"] for t in turns]
        self.assertIn("a", texts)
        self.assertIn("b", texts)
        self.assertNotIn("c", texts)

    def _bare_memory(self):
        cm = CompanionMemory.__new__(CompanionMemory)
        cm.companion_id = "abcd1234"
        cm.short_term = MagicMock()
        cm.short_term.user_id = 9
        return cm

    def test_persist_unpaid_skips_album(self):
        cm = self._bare_memory()
        cm.short_term.add.return_value = {
            "id": "t1",
            "content": "hello",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "send_status": "unpaid",
        }
        with patch("services.private_album.on_user_chat_message") as album:
            stored = cm.persist_unpaid_user_messages(["hello"])
        album.assert_not_called()
        cm.short_term.add.assert_called_once_with(
            "user", "hello", send_status="unpaid"
        )
        self.assertEqual(stored[0]["send_status"], "unpaid")

    def test_add_user_unpaid_skips_album(self):
        cm = self._bare_memory()
        cm.short_term.add.return_value = {"id": 1}
        with patch("services.private_album.on_user_chat_message") as album:
            ev = cm.add_user_message("x", send_status="unpaid")
        self.assertIsNone(ev)
        album.assert_not_called()

    def test_annotate_keeps_send_status(self):
        msg = {"role": "user", "content": "hi", "send_status": "unpaid"}
        out = annotate_chat_message(msg)
        self.assertEqual(out["send_status"], "unpaid")

    def test_catchup_skips_unpaid(self):
        from services import im_admin

        with patch(
            "services.memory.ShortTermMemory",
            return_value=SimpleNamespace(
                get_recent=lambda n: [
                    {"role": "user", "content": "later", "send_status": "unpaid"}
                ]
            ),
        ):
            self.assertIsNone(im_admin._pending_user_text_for_catchup(1, "abcd1234"))


if __name__ == "__main__":
    unittest.main()
