"""福利模板地区：Accept-Language 语种 → 商店国家码。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.benefit_titles import language_to_region


class TestLanguageToRegion(unittest.TestCase):
    def test_ja_maps_to_jp_not_ja(self):
        self.assertEqual(language_to_region("ja"), "JP")
        self.assertEqual(language_to_region("ja-JP"), "JP")
        self.assertEqual(language_to_region("ja,en;q=0.8"), "JP")

    def test_zh_ko_en(self):
        self.assertEqual(language_to_region("zh-CN"), "CN")
        self.assertEqual(language_to_region("zh"), "CN")
        self.assertEqual(language_to_region("zh-Hans-CN"), "CN")
        self.assertEqual(language_to_region("en-US"), "US")
        self.assertEqual(language_to_region("ko"), "KR")

    def test_frontend_lang_codes(self):
        self.assertEqual(language_to_region("en"), "US")
        self.assertEqual(language_to_region("EN"), "US")
        self.assertEqual(language_to_region("ja"), "JP")
        self.assertEqual(language_to_region("Ja"), "JP")
        self.assertEqual(language_to_region("ko"), "KR")
        self.assertEqual(language_to_region("KO"), "KR")
        self.assertEqual(language_to_region("pt"), "BR")
        self.assertEqual(language_to_region("PT"), "BR")
        self.assertEqual(language_to_region("es"), "ES")
        self.assertEqual(language_to_region("id"), "ID")
        self.assertEqual(language_to_region("ID"), "ID")
        self.assertEqual(language_to_region("ar"), "SA")
        self.assertEqual(language_to_region("ru"), "RU")
        self.assertEqual(language_to_region("Ru"), "RU")
        self.assertEqual(language_to_region("bg"), "BG")
        self.assertEqual(language_to_region("tr"), "TR")
        self.assertEqual(language_to_region("ir"), "IR")
        self.assertEqual(language_to_region("IR"), "IR")
        self.assertEqual(language_to_region("fa"), "IR")

    def test_empty(self):
        self.assertEqual(language_to_region(""), "")
        self.assertEqual(language_to_region(None), "")


if __name__ == "__main__":
    unittest.main()
