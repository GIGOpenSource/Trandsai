"""朋友圈自动评论/回复语种：跟帖子正文；无内容跟人设；回复跟被评语言。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from services.moments import _ai_comment_lang, _resolve_moment_interact_lang


def _companion(lang: str, city: str = "") -> SimpleNamespace:
    return SimpleNamespace(profile=SimpleNamespace(language=lang, city=city))


class TestMomentInteractLang(unittest.TestCase):
    def test_detects_caption_script(self):
        self.assertEqual(_resolve_moment_interact_lang("مرحبا كيفك", "en"), "ar")
        self.assertEqual(_resolve_moment_interact_lang("今天天气真好", "en"), "zh")

    def test_empty_or_emoji_uses_fallback(self):
        self.assertEqual(_resolve_moment_interact_lang("", "ja"), "ja")
        self.assertEqual(_resolve_moment_interact_lang("😊🔥", "ko"), "ko")

    def test_comment_follows_caption_not_commenter_persona(self):
        commenter = _companion("ja")
        poster = _companion("en")
        lang = _ai_comment_lang("今天也想你了", commenter, caption_lang="zh", poster=poster)
        self.assertEqual(lang, "zh")

    def test_empty_caption_uses_commenter_persona(self):
        commenter = _companion("ar")
        poster = _companion("zh")
        lang = _ai_comment_lang("", commenter, caption_lang="zh", poster=poster)
        self.assertEqual(lang, "ar")
        lang2 = _ai_comment_lang("   ", commenter, poster=poster)
        self.assertEqual(lang2, "ar")


if __name__ == "__main__":
    unittest.main()
