"""first_chat：只看是否第一次连上 IM，Say Hi 写过消息仍应 true。"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.user_first_chat import first_chat_hello_payload  # noqa: E402


class TestFirstChatHello(unittest.TestCase):
    def test_hello_payload(self):
        p = first_chat_hello_payload(12, "abcd1234xxxx", True)
        self.assertEqual(p["type"], "hello")
        self.assertTrue(p["first_chat"])
        self.assertEqual(p["user_id"], 12)
        self.assertEqual(p["companion_id"], "abcd1234")

    def test_claim_true_when_messages_exist_but_never_entered_im(self):
        from services import user_first_chat as m

        row = SimpleNamespace(turns=1, first_im_at=None, updated_at=None)
        db = SimpleNamespace()

        class _Ctx:
            def __enter__(self):
                return db

            def __exit__(self, *a):
                return False

        with patch.object(m, "get_db", return_value=_Ctx()), patch.object(
            m, "get_or_create_user_companion_state", return_value=row
        ):
            self.assertTrue(m.claim_first_chat(7, "abcd1234"))
            self.assertIsNotNone(row.first_im_at)

    def test_claim_false_when_already_entered(self):
        from services import user_first_chat as m

        now = datetime.now(timezone.utc)
        row = SimpleNamespace(turns=0, first_im_at=now, updated_at=now)
        db = SimpleNamespace()

        class _Ctx:
            def __enter__(self):
                return db

            def __exit__(self, *a):
                return False

        with patch.object(m, "get_db", return_value=_Ctx()), patch.object(
            m, "get_or_create_user_companion_state", return_value=row
        ):
            self.assertFalse(m.claim_first_chat(7, "abcd1234"))


if __name__ == "__main__":
    unittest.main()
