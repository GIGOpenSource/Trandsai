"""Unit tests for user chat unread (in-memory, no Redis)."""
import unittest
from unittest.mock import patch

from services import user_chat_unread as u


class TestUserChatUnread(unittest.TestCase):
    def setUp(self):
        u._mem.clear()

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    def test_incr_and_total(self, _ex, _redis):
        a = u.incr_unread(7, "abcd1234", delta=1)
        self.assertEqual(a["unread_count"], 1)
        self.assertEqual(u.get_total_unread(7), 1)
        b = u.incr_unread(7, "abcd1234", delta=2)
        self.assertEqual(b["unread_count"], 3)
        c = u.incr_unread(7, "zzzz9999", delta=1)
        self.assertEqual(c["unread_count"], 1)
        self.assertEqual(u.get_total_unread(7), 4)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    def test_watch_snapshot_includes_zeros(self, _ex, _redis):
        u.incr_unread(8, "aa000001", delta=5)
        snap = u.snapshot(8, ["aa000001", "bb000002"])
        self.assertEqual(snap["total_unread"], 5)
        self.assertEqual(snap["companions"][0]["unread_count"], 5)
        self.assertEqual(snap["companions"][1]["unread_count"], 0)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    def test_clear_one_keeps_others(self, _ex, _redis):
        u.incr_unread(9, "aa000001")
        u.incr_unread(9, "bb000002")
        info = u.clear_unread(9, "aa000001")
        self.assertEqual(info["unread_count"], 0)
        self.assertEqual(info["cleared_from"], 1)
        self.assertEqual(info["total_unread"], 1)
        self.assertEqual(u.get_unread(9, "bb000002"), 1)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    def test_note_skips_when_live(self, _ex, _redis):
        out = u.note_assistant_unread(10, "aa000001", delivered_live=True)
        self.assertIsNone(out)
        self.assertEqual(u.get_total_unread(10), 0)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    @patch.object(u, "_chat_session_open", return_value=False)
    @patch.object(u, "schedule_broadcast")
    def test_note_increments_when_offline(self, _sched, _open, _ex, _redis):
        info = u.note_assistant_unread(11, "aa000001")
        self.assertEqual(info["unread_count"], 1)
        self.assertEqual(info["total_unread"], 1)
        _sched.assert_called_once()
        payload = _sched.call_args[0][1]
        self.assertEqual(payload["type"], "unread")
        self.assertEqual(payload["companion_id"], "aa000001")
        self.assertEqual(payload["unread_count"], 1)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    @patch.object(u, "schedule_broadcast")
    def test_clear_notifies_only_if_had_unread(self, _sched, _ex, _redis):
        u.clear_user_chat_unread(12, "aa000001", notify=True)
        _sched.assert_not_called()
        u.incr_unread(12, "aa000001")
        u.clear_user_chat_unread(12, "aa000001", notify=True)
        _sched.assert_called_once()

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    def test_unread_is_per_user(self, _ex, _redis):
        u.incr_unread(21, "aa000001")
        u.incr_unread(22, "aa000001", delta=3)
        self.assertEqual(u.get_total_unread(21), 1)
        self.assertEqual(u.get_total_unread(22), 3)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value=set())
    def test_empty_clear_does_not_wipe_all(self, _ex, _redis):
        u.incr_unread(23, "aa000001")
        u.incr_unread(23, "bb000002")
        info = u.clear_user_chat_unread(23, "", notify=True)
        self.assertEqual(info["cleared_from"], 0)
        self.assertEqual(u.get_total_unread(23), 2)

    def test_parse_ids_and_hello(self):
        self.assertEqual(u.parse_companion_ids("aa000001, bb000002"), ["aa000001", "bb000002"])
        with patch.object(u, "get_redis_client", return_value=None):
            with patch.object(u, "_excluded_companion_ids", return_value=set()):
                hello = u.hello_payload(30)
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["user_id"], 30)
        self.assertTrue(hello["watch_required"])
        self.assertEqual(hello["heartbeat_sec"], u.INBOX_HEARTBEAT_SEC)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value={"aa000001"})
    def test_blocked_excluded_from_total(self, _ex, _redis):
        u.incr_unread(31, "aa000001", delta=5)
        u.incr_unread(31, "bb000002", delta=2)
        self.assertEqual(u.get_total_unread(31), 2)
        snap = u.snapshot(31, ["aa000001", "bb000002"])
        self.assertEqual(snap["total_unread"], 2)
        self.assertEqual(snap["companions"][0]["unread_count"], 0)
        self.assertEqual(snap["companions"][1]["unread_count"], 2)

    @patch.object(u, "get_redis_client", return_value=None)
    @patch.object(u, "_excluded_companion_ids", return_value={"aa000001"})
    @patch.object(u, "_chat_session_open", return_value=False)
    @patch.object(u, "schedule_broadcast")
    def test_note_skips_excluded(self, _sched, _open, _ex, _redis):
        self.assertIsNone(u.note_assistant_unread(32, "aa000001"))
        _sched.assert_not_called()
        self.assertEqual(u.get_unread(32, "aa000001"), 0)


if __name__ == "__main__":
    unittest.main()
