"""msg_strategy_meta canonicalize / alias helpers."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.msg_strategy_meta import canonicalize_product_event  # noqa: E402


class TestCanonicalizeProductEvent(unittest.TestCase):
    def test_strip_test_prefix(self):
        self.assertEqual(
            canonicalize_product_event("test_send_message_click_moment"),
            "send_message_click_moment",
        )

    def test_plain_event_unchanged(self):
        self.assertEqual(canonicalize_product_event("google_login_click"), "google_login_click")


class TestExpandLibraryTypeKeys(unittest.TestCase):
    def test_includes_test_prefix_variant(self):
        from services.msg_strategy_meta import expand_library_type_keys

        keys = expand_library_type_keys(["send_message_click_moment"])
        self.assertIn("send_message_click_moment", keys)
        self.assertIn("test_send_message_click_moment", keys)


if __name__ == "__main__":
    unittest.main()
