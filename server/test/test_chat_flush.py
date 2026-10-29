"""Tests for reliable chat flush queue helpers."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("redis", MagicMock())

import core.chat_cache as chat_cache  # noqa: E402


class TestFlushQueue(unittest.TestCase):
    @patch.object(chat_cache, "get_redis_client")
    def test_pop_moves_to_processing(self, mock_client_fn):
        r = MagicMock()
        mock_client_fn.return_value = r
        payload = json.dumps({"companion_id": "abc", "role": "user", "content": "hi"})
        r.rpoplpush.side_effect = [payload, None]

        items = chat_cache.pop_flush_batch(5)
        self.assertEqual(len(items), 1)
        r.rpoplpush.assert_called_with(chat_cache.FLUSH_QUEUE_KEY, chat_cache.FLUSH_PROCESSING_KEY)

    @patch.object(chat_cache, "get_redis_client")
    def test_ack_removes_from_processing(self, mock_client_fn):
        r = MagicMock()
        mock_client_fn.return_value = r
        items = [{"companion_id": "abc", "role": "user", "content": "hi"}]
        chat_cache.ack_flush_batch(items)
        r.pipeline.assert_called()

    @patch.object(chat_cache, "get_redis_client")
    def test_clear_marks_room_cleared_before_deleting_keys(self, mock_client_fn):
        r = MagicMock()
        mock_client_fn.return_value = r
        chat_cache.clear("c1", user_id=9)
        r.set.assert_called()
        key = r.set.call_args[0][0]
        self.assertTrue(str(key).endswith(":cleared_at"))
        r.delete.assert_called()

    @patch.object(chat_cache, "get_redis_client")
    def test_mark_room_cleared_requires_redis(self, mock_client_fn):
        mock_client_fn.return_value = None
        with self.assertRaises(RuntimeError):
            chat_cache.mark_room_cleared("c1", 9)

    @patch.object(chat_cache, "get_redis_client")
    def test_purge_room_cache_does_not_mark_cleared(self, mock_client_fn):
        r = MagicMock()
        mock_client_fn.return_value = r
        chat_cache.purge_room_cache("c1", user_id=9)
        r.set.assert_not_called()
        r.delete.assert_called()

    @patch.object(chat_cache, "get_room_cleared_at")
    def test_drop_flush_item_at_or_before_clear(self, mock_cleared):
        cleared = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
        mock_cleared.return_value = cleared
        old = {
            "companion_id": "c1",
            "user_id": 9,
            "timestamp": (cleared - timedelta(seconds=1)).isoformat(),
        }
        same = {"companion_id": "c1", "user_id": 9, "timestamp": cleared.isoformat()}
        newer = {
            "companion_id": "c1",
            "user_id": 9,
            "timestamp": (cleared + timedelta(seconds=1)).isoformat(),
        }
        self.assertTrue(chat_cache.should_drop_flush_item(old))
        self.assertTrue(chat_cache.should_drop_flush_item(same))
        self.assertFalse(chat_cache.should_drop_flush_item(newer))

    def test_message_at_or_before_clear_helper(self):
        cleared = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
        self.assertTrue(
            chat_cache.message_at_or_before_clear("2026-08-18T08:59:00+00:00", cleared)
        )
        self.assertFalse(
            chat_cache.message_at_or_before_clear("2026-08-18T09:00:01+00:00", cleared)
        )
        self.assertFalse(chat_cache.message_at_or_before_clear("2026-08-18T09:00:01+00:00", None))

    def test_flush_skips_cleared_items(self):
        try:
            import services.chat_persist as chat_persist
        except ImportError:
            self.skipTest("server deps not installed")
        items = [
            {
                "companion_id": "c1",
                "user_id": 9,
                "role": "user",
                "content": "old",
                "timestamp": "2026-08-18T08:59:00+00:00",
            }
        ]
        db = MagicMock()
        db.__enter__ = MagicMock(return_value=db)
        db.__exit__ = MagicMock(return_value=False)
        with patch.object(chat_persist, "should_drop_flush_item", return_value=True), patch.object(
            chat_persist, "get_db", return_value=db
        ), patch("core.chat_cache.get_redis_client", return_value=MagicMock()):
            n = chat_persist.flush_batch_to_pg(items)
        self.assertEqual(n, 0)
        db.add.assert_not_called()

    def test_flush_refuses_without_redis(self):
        try:
            import services.chat_persist as chat_persist
        except ImportError:
            self.skipTest("server deps not installed")
        items = [
            {
                "companion_id": "c1",
                "user_id": 9,
                "role": "user",
                "content": "old",
                "timestamp": "2026-08-18T08:59:00+00:00",
            }
        ]
        with patch("core.chat_cache.get_redis_client", return_value=None):
            with self.assertRaises(RuntimeError):
                chat_persist.flush_batch_to_pg(items)


if __name__ == "__main__":
    unittest.main()
