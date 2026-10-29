"""福利 app_key 解析与管理端筛选。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.app_key import (  # noqa: E402
    DEFAULT_APP_KEY,
    app_key_from_headers,
    normalize_app_key,
    resolve_app_key_filter,
)


class TestBenefitAppKey(unittest.TestCase):
    def test_default_trandsai(self):
        self.assertEqual(app_key_from_headers({}), DEFAULT_APP_KEY)
        self.assertEqual(normalize_app_key(None), "trandsai")
        self.assertEqual(normalize_app_key(""), "trandsai")

    def test_kaya_header(self):
        self.assertEqual(app_key_from_headers({"X-App-Key": "kaya"}), "kaya")
        self.assertEqual(app_key_from_headers({"x-app-key": "kaya"}), "kaya")

    def test_admin_filter_all(self):
        self.assertIsNone(resolve_app_key_filter(None))
        self.assertIsNone(resolve_app_key_filter(""))
        self.assertIsNone(resolve_app_key_filter("all"))
        self.assertEqual(resolve_app_key_filter("kaya"), "kaya")
        self.assertEqual(resolve_app_key_filter("trandsai"), "trandsai")


if __name__ == "__main__":
    unittest.main()
