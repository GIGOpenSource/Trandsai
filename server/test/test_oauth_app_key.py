"""OAuth 按 app_key 隔离账号。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.oauth_users import oauth_username  # noqa: E402


class TestOauthUsernameAppKey(unittest.TestCase):
    def test_trandsai_prefers_email(self):
        self.assertEqual(
            oauth_username("a@b.com", "apple", "SUB123", app_key="trandsai"),
            "a@b.com",
        )

    def test_kaya_uses_prefix_not_bare_email(self):
        name = oauth_username("a@b.com", "apple", "SUB123LONG", app_key="kaya")
        self.assertTrue(name.startswith("kay_"))
        self.assertIn("SUB123", name)
        self.assertNotEqual(name, "a@b.com")


if __name__ == "__main__":
    unittest.main()
