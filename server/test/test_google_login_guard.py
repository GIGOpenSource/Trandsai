"""Guard Google login (id_token only) and WS ticket helper."""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestGoogleLoginGuard(unittest.TestCase):
    def test_handler_requires_id_token_not_if_true(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "google_login.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("if True:", src)
        self.assertIn("_verify_google_id_token", src)
        self.assertIn("idToken", src)
        self.assertIn("id_token", src)
        try:
            from api import google_login as gl

            handler_src = inspect.getsource(gl.google_login)
            self.assertNotIn("if True", handler_src)
            self.assertIn("_verify_google_id_token", handler_src)
        except ModuleNotFoundError:
            pass


class TestWsTicketHelper(unittest.TestCase):
    def test_ticket_helper_source_is_one_time(self):
        path = os.path.join(os.path.dirname(__file__), "..", "core", "auth.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def verify_ws_ticket", src)
        self.assertIn("ws_ticket:", src)
        self.assertTrue("getdel" in src or "DEL" in src)

    def test_verify_ws_ticket_is_one_time(self):
        try:
            from core import auth as auth_mod
        except ModuleNotFoundError:
            self.skipTest("auth dependencies not installed")

        store = {}

        fake = MagicMock()

        def _setex(key, ttl, value):
            store[key] = value
            return True

        def _getdel(key):
            return store.pop(key, None)

        fake.setex.side_effect = _setex
        fake.getdel.side_effect = _getdel

        with patch.object(auth_mod, "get_redis", return_value=fake):
            ticket = auth_mod.issue_ws_ticket(42, ttl=60)
            self.assertTrue(ticket)
            uid = auth_mod.verify_ws_ticket(ticket)
            self.assertEqual(uid, 42)
            self.assertIsNone(auth_mod.verify_ws_ticket(ticket))


if __name__ == "__main__":
    unittest.main()
