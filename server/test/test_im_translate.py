"""IM 翻译：Accept-Language → 百度 to 码、是否需要翻译。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

from services.im_translate import (
    baidu_to_lang_from_accept,
    google_target_from_to_lang,
    needs_translation,
    translate,
    translate_one,
    _cache,
)


class TestBaiduLangFromAccept(unittest.TestCase):
    def test_simplified_chinese(self):
        self.assertEqual(baidu_to_lang_from_accept("zh-CN"), "zh")
        self.assertEqual(baidu_to_lang_from_accept("zh-Hans"), "zh")
        self.assertEqual(baidu_to_lang_from_accept("zh"), "zh")
        self.assertEqual(baidu_to_lang_from_accept("cn"), "zh")
        self.assertEqual(baidu_to_lang_from_accept(None), "zh")
        self.assertEqual(baidu_to_lang_from_accept(""), "zh")

    def test_traditional_chinese(self):
        self.assertEqual(baidu_to_lang_from_accept("zh-TW"), "cht")
        self.assertEqual(baidu_to_lang_from_accept("zh-HK"), "cht")
        self.assertEqual(baidu_to_lang_from_accept("zh-Hant"), "cht")

    def test_other_langs(self):
        self.assertEqual(baidu_to_lang_from_accept("en-US,en;q=0.9"), "en")
        self.assertEqual(baidu_to_lang_from_accept("ja-JP"), "jp")
        self.assertEqual(baidu_to_lang_from_accept("ko-KR"), "kor")
        self.assertEqual(baidu_to_lang_from_accept("pt-BR"), "pt")
        self.assertEqual(baidu_to_lang_from_accept("es-MX"), "spa")
        self.assertEqual(baidu_to_lang_from_accept("fr-FR"), "fra")
        self.assertEqual(baidu_to_lang_from_accept("id-ID"), "id")
        self.assertEqual(baidu_to_lang_from_accept("bg"), "bul")
        self.assertEqual(baidu_to_lang_from_accept("tr"), "tr")
        self.assertEqual(baidu_to_lang_from_accept("ir"), "per")
        self.assertEqual(baidu_to_lang_from_accept("fa"), "per")


class TestNeedsTranslation(unittest.TestCase):
    def test_to_zh(self):
        self.assertTrue(needs_translation("hi I am tovoy. is a tester", "zh"))
        self.assertTrue(needs_translation("hi I said '你好'", "zh"))
        self.assertFalse(needs_translation("你好，今天天气不错", "zh"))
        self.assertFalse(needs_translation("", "zh"))

    def test_to_en(self):
        self.assertFalse(needs_translation("hi I am tovoy", "en"))
        self.assertTrue(needs_translation("你好，今天天气不错", "en"))
        self.assertTrue(
            needs_translation(
                "Dejé descansar el lápiz un momento y me dio curiosidad saber de ti. ¿Cómo ha estado tu día?",
                "en",
            )
        )
        self.assertTrue(needs_translation("How has your day been?", "spa"))


class TestTranslateOneSkip(unittest.TestCase):
    def test_skip_already_zh(self):
        out = translate_one("你好啊", "zh")
        self.assertTrue(out["skipped"])
        self.assertEqual(out["translated"], "你好啊")
        self.assertEqual(out["target_lang"], "zh")
        self.assertIsNone(out["provider"])

    def test_spanish_to_en_not_skipped(self):
        src = "Dejé descansar el lápiz un momento y me dio curiosidad saber de ti. ¿Cómo ha estado tu día?"
        with mock.patch("services.im_translate._google_translate", return_value="I put the pencil down for a moment."), \
             mock.patch("services.im_translate._baidu_translate") as b:
            out = translate_one(src, "en")
            self.assertFalse(out["skipped"])
            self.assertEqual(out["provider"], "google")
            b.assert_not_called()


class TestGoogleTarget(unittest.TestCase):
    def test_baidu_codes(self):
        self.assertEqual(google_target_from_to_lang("zh"), "zh-CN")
        self.assertEqual(google_target_from_to_lang("cht"), "zh-TW")
        self.assertEqual(google_target_from_to_lang("jp"), "ja")
        self.assertEqual(google_target_from_to_lang("kor"), "ko")
        self.assertEqual(google_target_from_to_lang("spa"), "es")
        self.assertEqual(google_target_from_to_lang("per"), "fa")
        self.assertEqual(google_target_from_to_lang("bul"), "bg")

    def test_frontend_short_codes(self):
        self.assertEqual(google_target_from_to_lang("ja"), "ja")
        self.assertEqual(google_target_from_to_lang("ko"), "ko")
        self.assertEqual(google_target_from_to_lang("ir"), "fa")
        self.assertEqual(google_target_from_to_lang("en"), "en")


class TestTranslateFallback(unittest.TestCase):
    def setUp(self):
        with _cache_lock_safe():
            _cache.clear()

    def test_google_first(self):
        with mock.patch("services.im_translate._google_translate", return_value="Hello") as g, \
             mock.patch("services.im_translate._baidu_translate") as b:
            self.assertEqual(translate("你好", "en"), "Hello")
            g.assert_called_once()
            b.assert_not_called()

    def test_baidu_when_google_fails(self):
        with mock.patch("services.im_translate._google_translate", return_value=None), \
             mock.patch("services.im_translate._baidu_translate", return_value="Hello"):
            self.assertEqual(translate("你好", "en"), "Hello")

    def test_baidu_when_google_returns_same_text(self):
        src = "Dejé descansar el lápiz un momento."
        with mock.patch("services.im_translate._google_translate", return_value=src), \
             mock.patch("services.im_translate._baidu_translate", return_value="I put the pencil down for a moment.") as b:
            out = translate_one(src, "en")
            self.assertEqual(out["provider"], "baidu")
            self.assertFalse(out["skipped"])
            b.assert_called_once()

    def test_translate_one_provider_google(self):
        with mock.patch("services.im_translate._google_translate", return_value="Hello"), \
             mock.patch("services.im_translate._baidu_translate") as b:
            out = translate_one("你好", "en")
            self.assertEqual(out["provider"], "google")
            self.assertEqual(out["translated"], "Hello")
            self.assertFalse(out["skipped"])
            b.assert_not_called()

    def test_translate_one_provider_baidu(self):
        with mock.patch("services.im_translate._google_translate", return_value=None), \
             mock.patch("services.im_translate._baidu_translate", return_value="Hello"):
            out = translate_one("你好", "en")
            self.assertEqual(out["provider"], "baidu")
            self.assertEqual(out["translated"], "Hello")

    def test_cache_keeps_provider(self):
        with mock.patch("services.im_translate._google_translate", return_value="Hello") as g, \
             mock.patch("services.im_translate._baidu_translate") as b:
            first = translate_one("你好", "en")
            second = translate_one("你好", "en")
            self.assertEqual(first["provider"], "google")
            self.assertEqual(second["provider"], "google")
            g.assert_called_once()
            b.assert_not_called()


def _cache_lock_safe():
    from services.im_translate import _cache_lock
    return _cache_lock


if __name__ == "__main__":
    unittest.main()
