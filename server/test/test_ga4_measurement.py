"""GA4 Measurement Protocol helpers for gclid attribution."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ga4_measurement import build_mp_payload, send_gclid_to_ga4


class TestGa4Measurement(unittest.TestCase):
    def test_build_requires_gclid(self):
        self.assertIsNone(build_mp_payload({"device_id": "d1", "event_type": "install"}))

    def test_build_payload_maps_events(self):
        body = build_mp_payload(
            {
                "gclid": "EAIa_test_gclid",
                "device_id": "dev-1",
                "app_instance_id": "firebase-inst-1",
                "event_type": "register",
                "utm_source": "google",
                "utm_medium": "cpc",
                "utm_campaign": "spring",
                "user_id": 63,
            }
        )
        self.assertIsNotNone(body)
        assert body is not None
        self.assertEqual(body["app_instance_id"], "firebase-inst-1")
        self.assertEqual(body["client_id"], "dev-1")
        self.assertEqual(body["user_id"], "63")
        ev = body["events"][0]
        self.assertEqual(ev["name"], "sign_up")
        self.assertEqual(ev["params"]["gclid"], "EAIa_test_gclid")
        self.assertEqual(ev["params"]["source"], "google")
        self.assertEqual(ev["params"]["medium"], "cpc")

    @patch.dict(
        os.environ,
        {
            "GA4_MEASUREMENT_ID": "",
            "GA4_API_SECRET": "",
            "firebase_ads_key": "",
            "FIREBASE_ADS_KEY": "",
        },
        clear=False,
    )
    def test_send_skips_without_config(self):
        out = send_gclid_to_ga4({"gclid": "x", "device_id": "d"})
        self.assertFalse(out["ok"])
        self.assertEqual(out.get("skipped"), "not_configured_or_disabled")

    @patch.dict(
        os.environ,
        {
            "GA4_MEASUREMENT_ID": "G-TEST",
            "GA4_API_SECRET": "",
            "firebase_ads_key": "alias-secret",
            "GA4_MP_ENABLED": "1",
        },
        clear=False,
    )
    @patch("services.ga4_measurement.urllib.request.urlopen")
    def test_send_accepts_firebase_ads_key_alias(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 204
        resp.read.return_value = b""
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        mock_urlopen.return_value = resp

        out = send_gclid_to_ga4(
            {"gclid": "EAIa_abc", "device_id": "d1", "event_type": "install"}
        )
        self.assertTrue(out["ok"])
        req = mock_urlopen.call_args[0][0]
        self.assertIn("api_secret=alias-secret", req.full_url)


if __name__ == "__main__":
    unittest.main()
