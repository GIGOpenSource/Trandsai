"""Tests for reliable chat flush queue helpers."""
import json
import unittest
from unittest.mock import MagicMock, patch


class TestFlushQueue(unittest.TestCase):
    @patch("core.chat_cache.get_redis_client")
    def test_pop_moves_to_processing(self, mock_client_fn):
        from core.chat_cache import FLUSH_PROCESSING_KEY, FLUSH_QUEUE_KEY, pop_flush_batch

        r = MagicMock()
        mock_client_fn.return_value = r
        payload = json.dumps({"companion_id": "abc", "role": "user", "content": "hi"})
        r.rpoplpush.side_effect = [payload, None]

        items = pop_flush_batch(5)
        self.assertEqual(len(items), 1)
        r.rpoplpush.assert_called_with(FLUSH_QUEUE_KEY, FLUSH_PROCESSING_KEY)

    @patch("core.chat_cache.get_redis_client")
    def test_ack_removes_from_processing(self, mock_client_fn):
        from core.chat_cache import ack_flush_batch

        r = MagicMock()
        mock_client_fn.return_value = r
        items = [{"companion_id": "abc", "role": "user", "content": "hi"}]
        ack_flush_batch(items)
        r.pipeline.assert_called()


if __name__ == "__main__":
    unittest.main()
