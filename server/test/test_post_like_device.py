"""登录点赞写入的 device_id 不能和别人的设备号撞唯一键。"""
from __future__ import annotations

import os
import sys
import unittest

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from services.posts import _store_like_device, toggle_post_like


class TestStoreLikeDevice(unittest.TestCase):
    def test_logged_in_uses_user_slot(self):
        self.assertEqual(_store_like_device(92, "test-device-001"), "u:92")
        self.assertEqual(_store_like_device(131, "anonymous"), "u:131")

    def test_guest_keeps_real_device(self):
        self.assertEqual(_store_like_device(None, "test-device-001"), "test-device-001")

    def test_guest_anonymous_fallback(self):
        self.assertEqual(_store_like_device(None, ""), "anonymous")
        self.assertEqual(_store_like_device(None, "anonymous"), "anonymous")

    def test_toggle_recounts_from_like_rows(self):
        import inspect

        src = inspect.getsource(toggle_post_like)
        self.assertIn("_sync_post_likes_count", src)
        self.assertNotIn("likes_count + 1", src)


if __name__ == "__main__":
    unittest.main()
