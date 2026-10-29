"""ZeasAds HMAC；lookup 失败不挡。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.recommend_ua import ua_tags_from_payload, verify_audience_hmac, _hmac_hex


class TestAudienceHmac(unittest.TestCase):
    def test_verify(self):
        import services.recommend_ua as m
        old = m.ZEASADS_HMAC_SECRET
        m.ZEASADS_HMAC_SECRET = "secret"
        try:
            body = b'{"ua_tags":["ua:channel:tiktok"]}'
            sig = _hmac_hex(body, "secret")
            self.assertTrue(verify_audience_hmac(body, sig))
            self.assertTrue(verify_audience_hmac(body, "sha256=" + sig))
            self.assertFalse(verify_audience_hmac(body, "deadbeef"))
        finally:
            m.ZEASADS_HMAC_SECRET = old

    def test_payload_tags(self):
        tags = ua_tags_from_payload({
            "channel": "tiktok",
            "campaign": "spring",
            "audience": "lookalike",
        })
        self.assertIn("ua:channel:tiktok", tags)
        self.assertIn("ua:campaign:spring", tags)
        self.assertIn("audience:lookalike", tags)


if __name__ == "__main__":
    unittest.main()
