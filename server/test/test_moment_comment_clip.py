"""朋友圈评论截断：不再 100 字硬切。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from services.moments import _clip_moment_comment


class TestClipMomentComment(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(_clip_moment_comment("下一张呢"), "下一张呢")

    def test_does_not_hard_cut_at_100(self):
        text = "a" * 150
        out = _clip_moment_comment(text, max_len=280)
        self.assertEqual(out, text)

    def test_cuts_at_sentence(self):
        text = "Hello there, this is a full sentence. And then more words that go on and on beyond the limit."
        out = _clip_moment_comment(text, max_len=50)
        self.assertTrue(out.endswith("."))
        self.assertLessEqual(len(out), 50)


if __name__ == "__main__":
    unittest.main()
