"""接管空闲超时：心跳不续期；cutoff 用 UTC naive。"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class TestTakeoverIdleHelpers(unittest.TestCase):
    def test_session_ws_ping_does_not_touch(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "admin_sessions.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index('async def ws_admin_im(')
        block = src[start : start + 8000]
        ping_at = block.index('if mtype in ("ping", "pong")')
        ping_block = block[ping_at : ping_at + 1200]
        self.assertIn('"takeover_touched": False', ping_block)
        self.assertNotIn("touch_takeover_activity(user_id, cid)", ping_block)
        # 发消息仍续期
        self.assertIn("touch_takeover_activity(user_id, cid)", block)

    def test_idle_remaining_naive_utc(self):
        from services.im_admin import idle_remaining_seconds

        with patch("services.im_admin.takeover_idle_timeout_seconds", return_value=300):
            base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=100)
            rem = idle_remaining_seconds(base, None)
            self.assertIsNotNone(rem)
            self.assertLessEqual(rem, 200)
            self.assertGreater(rem, 150)

    def test_list_idle_uses_naive_cutoff(self):
        path = os.path.join(os.path.dirname(__file__), "..", "services", "im_admin.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def list_idle_takeovers")
        end = src.index("\ndef ensure_user_companion_state", start)
        block = src[start:end]
        self.assertIn("_utc_now_naive()", block)
        self.assertIn("idle_remaining_seconds", block)


if __name__ == "__main__":
    unittest.main()
