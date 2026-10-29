"""generate 回传字段随 lang 本地化；入库仍可归一为 男/女 与英文 key。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.gender import display_gender, stored_gender
from services.persona_axes import localize_axes_for_display, normalize_axes_for_storage


class TestGenderDisplay(unittest.TestCase):
    def test_display_follows_lang(self):
        self.assertEqual(display_gender("女", "ko"), "여")
        self.assertEqual(display_gender("男", "en"), "male")
        self.assertEqual(display_gender("女", "zh"), "女")

    def test_orientation_display_ko(self):
        from services.recommend_tags import canonical_orientation, display_sexual_orientation

        self.assertEqual(display_sexual_orientation("heterosexual", "ko"), "이성애")
        self.assertEqual(canonical_orientation("이성애"), "heterosexual")


class TestPersonaAxesLocalized(unittest.TestCase):
    def test_display_axes_use_labels(self):
        keys = {
            "career_class": "creative",
            "attachment": "anxious",
            "conflict_style": "cool_down",
            "life_pace": "irregular",
            "interest_domain": "gaming",
        }
        labels = localize_axes_for_display(keys, "ko")
        self.assertEqual(labels["career_class"], "크리에이티브")
        self.assertEqual(labels["attachment"], "불안 애착")
        self.assertEqual(labels["interest_domain"], "게임")

    def test_storage_normalizes_labels(self):
        raw = {
            "career_class": "크리에이티브",
            "attachment": "불안 애착",
            "conflict_style": "식힌 뒤 대화",
            "life_pace": "불규칙",
            "interest_domain": "게임",
        }
        self.assertEqual(
            normalize_axes_for_storage(raw),
            {
                "career_class": "creative",
                "attachment": "anxious",
                "conflict_style": "cool_down",
                "life_pace": "irregular",
                "interest_domain": "gaming",
            },
        )

    def test_storage_roundtrip_from_ko_labels(self):
        raw = {
            "career_class": "크리에이티브",
            "attachment": "불안 애착",
            "conflict_style": "식힌 뒤 대화",
            "life_pace": "불규칙",
            "interest_domain": "게임",
        }
        keys = normalize_axes_for_storage(raw)
        self.assertEqual(keys["career_class"], "creative")
        self.assertEqual(stored_gender("여"), "女")
        self.assertEqual(localize_axes_for_display(keys, "ko")["attachment"], "불안 애착")

    def test_tr_axes_use_local_labels(self):
        keys = {
            "career_class": "student",
            "attachment": "secure",
            "conflict_style": "direct",
            "life_pace": "slow",
            "interest_domain": "sports",
        }
        labels = localize_axes_for_display(keys, "tr")
        self.assertEqual(labels["career_class"], "öğrenci")
        self.assertEqual(labels["attachment"], "güvenli bağlanma")
        self.assertEqual(labels["conflict_style"], "doğrudan yüzleşme")
        self.assertEqual(labels["interest_domain"], "spor")
        self.assertEqual(
            normalize_axes_for_storage({"career_class": "öğrenci"})["career_class"],
            "student",
        )


if __name__ == "__main__":
    unittest.main()
