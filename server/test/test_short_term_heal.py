"""ShortTermMemory heal：未 flush 的 Redis 新消息不得被更旧的 PG 覆盖。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for _mod in (
    "chromadb",
    "chromadb.config",
    "chromadb.utils.embedding_functions",
    "openai",
    "sqlalchemy",
    "sqlalchemy.orm",
    "redis",
    "core.database",
):
    sys.modules.setdefault(_mod, MagicMock())


def _msg(ts: str, role: str, content: str, msg_id=None) -> dict:
    return {"id": msg_id, "role": role, "content": content, "timestamp": ts}


class TestHealShortFromPg(unittest.TestCase):
    def setUp(self):
        try:
            from services.memory import ShortTermMemory, _ring_buffer_lock, _ring_buffers
        except Exception as e:
            self.skipTest(f"memory dependencies not installed: {e}")
        self.ShortTermMemory = ShortTermMemory
        with _ring_buffer_lock:
            _ring_buffers.clear()
        self.mem = ShortTermMemory("c1", user_id=9)

    def tearDown(self):
        try:
            from services.memory import _ring_buffer_lock, _ring_buffers
        except Exception:
            return
        with _ring_buffer_lock:
            _ring_buffers.clear()

    def test_does_not_purge_when_redis_is_newer_but_shorter(self):
        pg = [_msg(f"2026-08-19T10:00:{i:02d}+00:00", "assistant", f"old{i}", i) for i in range(20)]
        hot = [
            _msg("2026-08-19T10:01:00+00:00", "user", "hello", "t1"),
            _msg("2026-08-19T10:01:01+00:00", "assistant", "hi", "t2"),
        ]
        with patch.object(self.mem, "_pg_get_recent", return_value=pg), patch(
            "services.memory.purge_room_cache"
        ) as purge, patch("services.memory.redis_warm_from_db") as warm:
            out = self.mem._heal_short_from_pg(hot, n=20, offset=0)
        purge.assert_not_called()
        warm.assert_not_called()
        self.assertEqual(out[-2]["content"], "hello")
        self.assertEqual(out[-1]["content"], "hi")
        self.assertEqual(len(out), 20)
        self.assertEqual(out[0]["content"], "old2")

    def test_empty_hot_warms_from_pg(self):
        pg = [_msg("2026-08-19T10:00:00+00:00", "user", "a", 1)]
        with patch.object(self.mem, "_pg_get_recent", return_value=pg), patch(
            "services.memory.purge_room_cache"
        ) as purge, patch("services.memory.redis_warm_from_db") as warm:
            out = self.mem._heal_short_from_pg([], n=20, offset=0)
        purge.assert_called_once()
        warm.assert_called_once()
        self.assertEqual(out, pg)

    def test_pg_strictly_newer_replaces_stale_hot(self):
        hot = [_msg("2026-08-19T10:00:00+00:00", "user", "stale", 1)]
        pg = [
            _msg("2026-08-19T10:00:00+00:00", "user", "stale", 1),
            _msg("2026-08-19T10:02:00+00:00", "assistant", "flushed", 2),
        ]
        with patch.object(self.mem, "_pg_get_recent", return_value=pg), patch(
            "services.memory.purge_room_cache"
        ) as purge, patch("services.memory.redis_warm_from_db") as warm:
            out = self.mem._heal_short_from_pg(hot, n=20, offset=0)
        purge.assert_called_once()
        warm.assert_called_once()
        self.assertEqual(out[-1]["content"], "flushed")

    def test_get_recent_incremental_window_keeps_unflushed(self):
        pg = [_msg(f"2026-08-19T10:00:{i:02d}+00:00", "assistant", f"old{i}", i) for i in range(20)]
        redis_rows = [
            *pg[:20],
            _msg("2026-08-19T10:01:00+00:00", "user", "hello", "t1"),
            _msg("2026-08-19T10:01:01+00:00", "assistant", "new reply", "t2"),
        ]
        with patch("services.memory.bridge_enabled", return_value=True), patch(
            "services.memory.redis_get_recent", return_value=redis_rows
        ), patch.object(self.mem, "_pg_get_recent", return_value=pg), patch(
            "services.memory.purge_room_cache"
        ) as purge, patch("services.memory.get_room_cleared_at", return_value=None):
            out = self.mem.get_recent(80, 0)
        purge.assert_not_called()
        self.assertEqual(out[-1]["content"], "new reply")
        self.assertEqual(out[-2]["content"], "hello")

    def test_get_recent_drops_stale_ring_after_clear(self):
        from datetime import datetime, timezone

        from services.memory import _ring_buffer_lock, _ring_buffers

        cleared = datetime(2026, 8, 19, 10, 5, tzinfo=timezone.utc)
        stale = [
            _msg("2026-08-19T10:00:00+00:00", "user", "old user", "t1"),
            _msg("2026-08-19T10:00:01+00:00", "assistant", "old bot", "t2"),
        ]
        with _ring_buffer_lock:
            _ring_buffers[self.mem._buf_key()] = list(stale)

        with patch("services.memory.bridge_enabled", return_value=True), patch(
            "services.memory.redis_get_recent", return_value=[]
        ), patch.object(self.mem, "_pg_get_recent", return_value=[]), patch(
            "services.memory.get_room_cleared_at", return_value=cleared
        ):
            out = self.mem.get_recent(20, 0)

        self.assertEqual(out, [])
        with _ring_buffer_lock:
            self.assertNotIn(self.mem._buf_key(), _ring_buffers)

    def test_get_recent_keeps_messages_after_clear_tombstone(self):
        from datetime import datetime, timezone

        cleared = datetime(2026, 8, 19, 10, 5, tzinfo=timezone.utc)
        redis_rows = [
            _msg("2026-08-19T10:00:00+00:00", "user", "old", "t0"),
            _msg("2026-08-19T10:06:00+00:00", "user", "new", "t1"),
            _msg("2026-08-19T10:06:01+00:00", "assistant", "hi", "t2"),
        ]
        with patch("services.memory.bridge_enabled", return_value=True), patch(
            "services.memory.redis_get_recent", return_value=redis_rows
        ), patch.object(self.mem, "_pg_get_recent", return_value=[]), patch(
            "services.memory.get_room_cleared_at", return_value=cleared
        ):
            out = self.mem.get_recent(20, 0)

        self.assertEqual([m["content"] for m in out], ["new", "hi"])


if __name__ == "__main__":
    unittest.main()
