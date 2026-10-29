"""默认亲密档位：max(用户, 默认) 进提示词。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.affection_floor import (
    clamp_affection_floor,
    effective_prompt_affection,
    normalize_floor_config_json,
)


class TestClamp(unittest.TestCase):
    def test_range(self):
        self.assertEqual(clamp_affection_floor(50), 50)
        self.assertEqual(clamp_affection_floor(10), 20)
        self.assertEqual(clamp_affection_floor(200), 100)


class TestEffective(unittest.TestCase):
    def test_ads_user_below_floor(self):
        with mock.patch(
            "services.affection_floor.get_default_affection_floor", return_value=80.0
        ):
            self.assertEqual(effective_prompt_affection(10, is_ads_user=True), 80.0)

    def test_ads_user_above_floor(self):
        with mock.patch(
            "services.affection_floor.get_default_affection_floor", return_value=50.0
        ):
            self.assertEqual(effective_prompt_affection(90, is_ads_user=True), 90.0)

    def test_organic_ignores_floor(self):
        with mock.patch(
            "services.affection_floor.get_default_affection_floor", return_value=90.0
        ):
            self.assertEqual(effective_prompt_affection(0, is_ads_user=False), 0.0)
            self.assertEqual(effective_prompt_affection(12, is_ads_user=False), 12.0)

    def test_disabled_ads(self):
        with mock.patch(
            "services.affection_floor.get_default_affection_floor", return_value=None
        ):
            self.assertEqual(effective_prompt_affection(12, is_ads_user=True), 12.0)

    def test_normalize(self):
        d = normalize_floor_config_json({"default_affection_floor": 15})
        self.assertEqual(d["default_affection_floor"], 20)


class TestAdsTags(unittest.TestCase):
    def test_source_ads(self):
        from services.affection_floor import ua_tags_indicate_ads

        self.assertTrue(ua_tags_indicate_ads(["ua:source:ads"]))
        self.assertTrue(ua_tags_indicate_ads(["ua:campaign:c1"]))
        self.assertFalse(ua_tags_indicate_ads([]))
        self.assertFalse(ua_tags_indicate_ads(["purpose:romance"]))
        self.assertFalse(ua_tags_indicate_ads(["ua:channel:tiktok"]))


if __name__ == "__main__":
    unittest.main()
