"""Growth attribution: product events + firebase mirror + gclid fallbacks."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import services.growth_attribution as ga  # noqa: E402


class TestBuildAttributionProductFields(unittest.TestCase):
    def test_product_event_and_firebase_mirror(self):
        req = MagicMock()
        req.client = None
        req.headers = {}
        fields = ga.build_attribution_fields(
            req,
            {
                "event_type": "product",
                "device_id": "dev-1",
                "product_event": "app_launch",
                "firebase_event_name": "app_launch",
                "firebase_event_params": {
                    "device": "android/Pixel 7",
                    "launch_type": "cold",
                    "gclid": "GCLID_FROM_FB",
                    "utm_source": "google",
                },
                "app_env": "test",
            },
        )
        self.assertEqual(fields["event_type"], "product")
        self.assertEqual(fields["product_event"], "app_launch")
        self.assertEqual(fields["firebase_event_name"], "app_launch")
        self.assertEqual(fields["app_env"], "test")
        self.assertEqual(fields["gclid"], "GCLID_FROM_FB")
        self.assertEqual(fields["utm_source"], "google")
        self.assertEqual(fields["firebase_event_params"]["launch_type"], "cold")

    def test_product_event_fallback_from_firebase_name(self):
        req = MagicMock()
        req.client = None
        req.headers = {}
        fields = ga.build_attribution_fields(
            req,
            {
                "event_type": "product",
                "device_id": "dev-2",
                "firebase_event_name": "home_page_show",
                "firebase_event_params": {"page": "home"},
            },
        )
        self.assertEqual(fields["product_event"], "home_page_show")
        self.assertEqual(fields["firebase_event_name"], "home_page_show")

    def test_gclid_priority_top_level_over_firebase(self):
        gclid, _ = ga.extract_gclid_and_campaign(
            gclid="TOP",
            raw_payload={"gclid": "RAW"},
            firebase_event_params={"gclid": "FB"},
        )
        self.assertEqual(gclid, "TOP")

    def test_gclid_from_firebase_when_missing_elsewhere(self):
        gclid, _ = ga.extract_gclid_and_campaign(
            gclid="",
            raw_payload={},
            firebase_event_params={"gclid": "ONLY_FB"},
        )
        self.assertEqual(gclid, "ONLY_FB")


if __name__ == "__main__":
    unittest.main()
