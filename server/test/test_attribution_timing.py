"""归因时间中数：时长格式化单测。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.attribution_timing import _format_duration, _median  # noqa: E402


class TestAttributionTimingHelpers(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(_format_duration(0), "0秒")
        self.assertEqual(_format_duration(45), "45秒")
        self.assertEqual(_format_duration(3600), "1小时")
        self.assertEqual(_format_duration(90000), "1天1小时")

    def test_median(self):
        self.assertIsNone(_median([]))
        self.assertEqual(_median([1.0, 2.0, 3.0]), 2.0)
        self.assertEqual(_median([1.0, 2.0, 3.0, 4.0]), 2.5)


if __name__ == "__main__":
    unittest.main()
