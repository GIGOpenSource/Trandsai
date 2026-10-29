"""一键填充输出语种、随机年龄收窄、出图年轻外观锁。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestPersonaGenerateLang(unittest.TestCase):
    def test_body_lang_beats_accept_language(self):
        from services.prompts.resolve import resolve_persona_generate_lang

        self.assertEqual(
            resolve_persona_generate_lang({"lang": "en"}, accept_language="zh-CN"),
            "en",
        )

    def test_accept_language_when_body_missing(self):
        from services.prompts.resolve import resolve_persona_generate_lang

        self.assertEqual(
            resolve_persona_generate_lang({}, accept_language="ja-JP"),
            "ja",
        )

    def test_default_en_without_city_infer(self):
        from services.prompts.resolve import resolve_persona_generate_lang

        self.assertEqual(resolve_persona_generate_lang({}), "en")
        # 即使传入中文城市相关字段也不推断
        self.assertEqual(
            resolve_persona_generate_lang({"city": "北京", "name": "测试"}),
            "en",
        )


class TestPersonaOutputLang(unittest.TestCase):
    def test_ui_lang_beats_chinese_city(self):
        from services.prompts.resolve import resolve_persona_output_lang

        self.assertEqual(
            resolve_persona_output_lang({"lang": "en"}, city="上海"),
            "en",
        )
        self.assertEqual(
            resolve_persona_output_lang({"language": "ja"}, city="北京"),
            "ja",
        )

    def test_accept_language_when_body_missing(self):
        from services.prompts.resolve import resolve_persona_output_lang

        self.assertEqual(
            resolve_persona_output_lang({}, accept_language="en-US", city="上海"),
            "en",
        )

    def test_city_fallback_when_no_ui_lang(self):
        from services.prompts.resolve import resolve_persona_output_lang

        self.assertEqual(resolve_persona_output_lang({}, city="New York"), "en")
        self.assertEqual(resolve_persona_output_lang({}, city="北京"), "zh")

    def test_generate_instruction_is_english_only(self):
        from services.culture_data import get_persona_generate_output_instruction

        instr = get_persona_generate_output_instruction("en")
        self.assertIn("English only", instr)
        self.assertIn("OUTPUT LANGUAGE", instr)
        zh = get_persona_generate_output_instruction("zh")
        self.assertIn("简体中文", zh)

    def test_arabic_accept_language_gets_arabic_instruction(self):
        from services.prompts.resolve import resolve_persona_generate_lang
        from services.culture_data import get_persona_generate_output_instruction
        from core.lang_catalog import persona_generate_human_message

        lang = resolve_persona_generate_lang({}, accept_language="ar-SA")
        self.assertEqual(lang, "ar")
        instr = get_persona_generate_output_instruction(lang)
        self.assertIn("العربية", instr)
        self.assertNotIn("简体中文", instr)
        human = persona_generate_human_message(lang)
        self.assertIn("العربية", human)

    def test_russian_maps_to_russian_output(self):
        from core.lang_catalog import persona_output_instruction

        instr = persona_output_instruction("ru")
        self.assertIn("Русский", instr)
        self.assertNotIn("简体中文", instr)

    def test_arabic_is_seed_lang_without_english_seed_note(self):
        from core.lang_catalog import PERSONA_SEED_LANGS, persona_output_instruction

        self.assertIn("ar", PERSONA_SEED_LANGS)
        instr = persona_output_instruction("ar")
        self.assertNotIn("Prompt seed fields", instr)
        self.assertIn("العربية", instr)


class TestRandomAgeRange(unittest.TestCase):
    def test_range_is_18_to_26(self):
        from services.culture_data import RANDOM_PROFILE_AGE_MAX, RANDOM_PROFILE_AGE_MIN

        self.assertEqual(RANDOM_PROFILE_AGE_MIN, 18)
        self.assertEqual(RANDOM_PROFILE_AGE_MAX, 26)

    def test_sampled_ages_stay_in_range(self):
        from services.culture_data import (
            RANDOM_PROFILE_AGE_MAX,
            RANDOM_PROFILE_AGE_MIN,
            build_batch_profiles,
        )

        ages = [build_batch_profiles("en", 1)[0]["age"] for _ in range(40)]
        self.assertTrue(all(RANDOM_PROFILE_AGE_MIN <= a <= RANDOM_PROFILE_AGE_MAX for a in ages))
        self.assertTrue(all(a <= 26 for a in ages))


class TestYouthfulImagePrompts(unittest.TestCase):
    def test_avatar_prompt_locks_youth_not_mature(self):
        from services.image_generation import generate_avatar_prompt

        prompt = generate_avatar_prompt({"gender": "女", "age": 32, "city": "New York", "personality": "沉稳"}, style="realistic")
        self.assertIn("AGE LOCK", prompt)
        self.assertIn("no wrinkles", prompt)
        self.assertNotIn("成熟优雅", prompt)
        self.assertNotIn("沉稳内敛", prompt)
        self.assertIn("young woman", prompt)

    def test_moment_person_prompt_locks_youth(self):
        from services.image_generation import generate_moment_image_prompt

        # 多抽几次，避免随机走到纯风景
        hits = []
        for _ in range(12):
            p, _style = generate_moment_image_prompt(
                "mirror selfie before heading out",
                profile={"gender": "男", "age": 30, "city": "London", "personality": "calm"},
            )
            hits.append(p)
            if "AGE LOCK" in p:
                break
        blob = "\n".join(hits)
        self.assertIn("AGE LOCK", blob)
        self.assertIn("young man", blob)
        self.assertNotIn("成熟优雅", blob)


if __name__ == "__main__":
    unittest.main()
