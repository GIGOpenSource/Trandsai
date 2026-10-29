"""persona generate 归一化：缺字段时不应输出（待完善）占位。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.companions import (
    _extract_json,
    _is_persona_placeholder,
    _merge_generate_response,
    _normalize_persona_result,
    _salvage_persona_fields,
)


class TestPersonaNormalize(unittest.TestCase):
    def test_placeholder_detection(self):
        self.assertTrue(_is_persona_placeholder("", "zh"))
        self.assertTrue(_is_persona_placeholder("（待完善）hobbies", "zh"))
        self.assertTrue(_is_persona_placeholder("(pending) hobbies", "en"))
        self.assertFalse(_is_persona_placeholder("喜欢夜跑", "zh"))

    def test_normalize_fills_details_without_pending(self):
        data = {
            "name": "小林",
            "city": "成都",
            "personality": "慢热",
            "_resolved_lang": "zh",
        }
        out = _normalize_persona_result({"appearance": "清秀"}, data)
        for key in (
            "hobbies",
            "values",
            "fears",
            "love_view",
            "daily_routine",
            "favorite_things",
            "speech_style",
        ):
            self.assertTrue(out[key])
            self.assertFalse(out[key].startswith("（待完善）"), msg=key)

    def test_salvage_truncated_json(self):
        raw = (
            '{"appearance": "年轻短发", "background": "在成都生活", '
            '"speech_style": "口语化", "hobbies": "骑行", "values": "真诚", '
            '"fears": "被误解", "love_view": "慢慢来", "daily_routine": "早起", '
            '"favorite_things": "火锅", "life_story": "成长于'
        )
        salvaged = _salvage_persona_fields(raw)
        self.assertEqual(salvaged.get("hobbies"), "骑行")
        self.assertEqual(salvaged.get("favorite_things"), "火锅")
        self.assertNotIn("life_story", salvaged)

    def test_extract_json_salvage_fallback(self):
        raw = (
            '{"appearance": "youthful", "hobbies": "hiking", "values": "honesty", '
            '"fears": "labels", "love_view": "slow burn", "daily_routine": "early", '
            '"favorite_things": "coffee", "life_story": "unfinished'
        )
        data = _extract_json(raw)
        self.assertEqual(data.get("hobbies"), "hiking")
        self.assertFalse(str(data.get("hobbies", "")).startswith("(pending)"))


class TestMergeGenerateResponse(unittest.TestCase):
    def test_omits_unused_frontend_fields(self):
        seed = {
            "name": "Charlotte",
            "city": "Boston",
            "region_label": "United States",
            "personality": "airheaded, brotherly",
            "persona_axes": {"career_class": "student"},
            "age": 19,
            "gender": "女",
            "sexual_orientation": "heterosexual",
            "country": "US",
            "region_key": "us",
            "mbti": "ISFJ",
            "_axis_keys": {"career_class": "student"},
            "personality_tags": ["airheaded"],
            "persona_axes_keys": {"career_class": "student"},
            "persona_axes_summary": "- Career class: student",
        }
        normalized = {
            "appearance": "Standing....",
            "background": "Growin...",
            "speech_style": "Speak....",
            "hobbies": "Mapping....",
            "values": "Deep....",
            "fears": "Secretly ....",
            "love_view": "Apprving....",
            "daily_routine": "Stado toa....",
            "favorite_things": "Sturd....",
            "life_story": "Grow...",
            "cultural_values": "Immersed...",
            "gender_perspective": "Charlotte...",
        }
        out = _merge_generate_response(seed, normalized, "en")
        for key in (
            "personality_labels",
            "personality_by_group",
            "personality_tags",
            "persona_axes_keys",
            "persona_axes_summary",
            "language",
            "_axis_keys",
        ):
            self.assertNotIn(key, out)
        self.assertEqual(out["persona_axes"], {"career_class": "student"})
        self.assertEqual(out["personality"], "airheaded, brotherly")
        self.assertEqual(out["name"], "Charlotte")


if __name__ == "__main__":
    unittest.main()
