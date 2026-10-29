"""亲密度：空关系卡不得打回 0；聊过天仍为 0 时按轮数补齐。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.affection_math import catch_up_stalled_affection, coalesce_affection_write


class TestCoalesceAffectionWrite(unittest.TestCase):
    def test_empty_card_does_not_zero_existing(self):
        self.assertEqual(coalesce_affection_write(4.2, 0), 4.2)
        self.assertEqual(coalesce_affection_write(4.2, 0.0), 4.2)

    def test_real_update_applies(self):
        self.assertEqual(coalesce_affection_write(4.2, 4.85), 4.85)
        self.assertEqual(coalesce_affection_write(0, 0.65), 0.65)

    def test_both_zero(self):
        self.assertEqual(coalesce_affection_write(0, 0), 0.0)


class TestCatchUpStalledAffection(unittest.TestCase):
    def test_seven_turns_stuck_at_zero(self):
        got = catch_up_stalled_affection(0, 7)
        self.assertGreater(got, 3.0)
        self.assertLess(got, 8.0)

    def test_no_turns_stays_zero(self):
        self.assertEqual(catch_up_stalled_affection(0, 0), 0.0)

    def test_healthy_value_unchanged(self):
        self.assertAlmostEqual(catch_up_stalled_affection(4.2, 7), 4.2)


if __name__ == "__main__":
    unittest.main()
