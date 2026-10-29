"""管理端媒体代理 URL 白名单。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.media_proxy import is_allowed_media_url, proxy_path_for  # noqa: E402


class TestMediaProxyAllowlist(unittest.TestCase):
    def test_gcs_bucket_ok(self):
        with patch.dict(os.environ, {"GCS_BUCKET": "trandsbucket"}, clear=False):
            self.assertTrue(
                is_allowed_media_url(
                    "https://storage.googleapis.com/trandsbucket/moments/a.png"
                )
            )

    def test_gcs_wrong_bucket_rejected(self):
        with patch.dict(os.environ, {"GCS_BUCKET": "trandsbucket"}, clear=False):
            self.assertFalse(
                is_allowed_media_url(
                    "https://storage.googleapis.com/otherbucket/moments/a.png"
                )
            )

    def test_random_host_rejected(self):
        with patch.dict(os.environ, {"GCS_BUCKET": "trandsbucket"}, clear=False):
            self.assertFalse(is_allowed_media_url("https://evil.example/x.png"))
            self.assertFalse(is_allowed_media_url("http://127.0.0.1/x.png"))

    def test_proxy_path(self):
        with patch.dict(os.environ, {"GCS_BUCKET": "trandsbucket"}, clear=False):
            u = "https://storage.googleapis.com/trandsbucket/moments/a.png"
            path = proxy_path_for(u)
            self.assertTrue(path.startswith("/api/admin/media/proxy?url="))
            self.assertIn("trandsbucket", path)


if __name__ == "__main__":
    unittest.main()
