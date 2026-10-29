import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.funnel_display import format_funnel_count_pct  # noqa: E402


class TestFunnelDisplay(unittest.TestCase):
    def test_login_col_is_100(self):
        self.assertEqual(format_funnel_count_pct(131, 131, is_login_col=True), "131（100%）")
        self.assertEqual(format_funnel_count_pct(45, 45, is_login_col=True), "45（100%）")

    def test_doc_examples_20261025(self):
        login = 131
        self.assertEqual(format_funnel_count_pct(55, login), "55（41.98%）")
        self.assertEqual(format_funnel_count_pct(40, login), "40（30.53%）")
        self.assertEqual(format_funnel_count_pct(0, login), "0（0%）")
        self.assertEqual(format_funnel_count_pct(41, login), "41（31.30%）")
        self.assertEqual(format_funnel_count_pct(59, login), "59（45.04%）")
        self.assertEqual(format_funnel_count_pct(4, login), "4（3.05%）")
        self.assertEqual(format_funnel_count_pct(2, login), "2（1.53%）")

    def test_zero_login(self):
        self.assertEqual(format_funnel_count_pct(0, 0, is_login_col=True), "0（—）")
        self.assertEqual(format_funnel_count_pct(3, 0), "3（—）")

    def test_can_exceed_100(self):
        self.assertEqual(format_funnel_count_pct(59, 45), "59（131.11%）")


if __name__ == "__main__":
    unittest.main()
