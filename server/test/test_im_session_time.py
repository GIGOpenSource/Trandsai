import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.im_session_time import (  # noqa: E402
    last_2_days_utc_range,
    parse_utc_datetime,
    resolve_list_time_range,
)


class TestImSessionTime(unittest.TestCase):
    def test_last_2_days_includes_yesterday_and_today(self):
        now = datetime(2026, 10, 28, 14, 0, 0, tzinfo=timezone.utc)
        start, end_inc, end_excl = last_2_days_utc_range(now)
        self.assertEqual(start, datetime(2026, 10, 27, 0, 0, 0))
        self.assertEqual(end_inc, datetime(2026, 10, 28, 23, 59, 59))
        self.assertEqual(end_excl, datetime(2026, 10, 29, 0, 0, 0))

    def test_default_mode_is_last_2_days(self):
        mode, start, end, end_excl = resolve_list_time_range(None, None, None)
        self.assertEqual(mode, "last_2_days")
        self.assertIsNotNone(start)
        self.assertIsNotNone(end_excl)
        self.assertLess(start, end_excl)

    def test_all_skips_range(self):
        mode, start, end, end_excl = resolve_list_time_range("all", None, None)
        self.assertEqual(mode, "all")
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertIsNone(end_excl)

    def test_custom_start_after_end_raises(self):
        with self.assertRaises(ValueError):
            resolve_list_time_range(
                "custom",
                "2026-10-28T12:00:00Z",
                "2026-10-27T12:00:00Z",
            )

    def test_parse_z_and_space(self):
        a = parse_utc_datetime("2026-10-27T00:00:00Z", field="start_at")
        b = parse_utc_datetime("2026-10-27 00:00:00", field="start_at")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
