"""X-App-Key → Apple aud / client_id 映射。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.app_key import apple_client_ids_for_app_key, app_key_from_headers  # noqa: E402


class TestAppleClientIdsForAppKey(unittest.TestCase):
    def test_trandsai_uses_service_and_bundle(self):
        env = {
            "APPLE_BUNDLE_ID": "com.gig.trandsai",
            "APPLE_SERVICE_ID": "com.gig.trandsai",
            "APPLE_CLIENT_IDS": "com.gig.kaya",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                apple_client_ids_for_app_key("trandsai"),
                ["com.gig.trandsai"],
            )
            self.assertEqual(
                apple_client_ids_for_app_key(""),
                ["com.gig.trandsai"],
            )

    def test_kaya_uses_client_ids_env(self):
        env = {
            "APPLE_SERVICE_ID": "com.gig.trandsai",
            "APPLE_CLIENT_IDS": "com.gig.kaya",
            "APPLE_BUNDLE_ID_KAYA": "",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                apple_client_ids_for_app_key("kaya"),
                ["com.gig.kaya"],
            )

    def test_header_kaya(self):
        self.assertEqual(app_key_from_headers({"X-App-Key": "kaya"}), "kaya")
        self.assertEqual(app_key_from_headers({}), "trandsai")


if __name__ == "__main__":
    unittest.main()
