"""标签组装 / 性格反解 / ops 合并 / NULL→[]。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.personality_catalog import keys_from_personality_text
from services.recommend_tags import (
    assemble_user_tags,
    assemble_user_tags_from_orm,
    gender_tag_from_companion,
    gender_tag_from_user,
    normalize_tag_list,
    object_matches_tags,
    orientation_tag,
    sync_companion_tags,
)


class TestGenderTags(unittest.TestCase):
    def test_male_female_zh_and_en(self):
        self.assertEqual(gender_tag_from_companion("男"), "gender:male")
        self.assertEqual(gender_tag_from_companion("女"), "gender:female")
        self.assertEqual(gender_tag_from_companion("male"), "gender:male")
        self.assertEqual(gender_tag_from_companion("FEMALE"), "gender:female")
        self.assertIsNone(gender_tag_from_companion("保密"))
        self.assertIsNone(gender_tag_from_companion(""))
        self.assertEqual(gender_tag_from_user("保密"), "gender:confidential")
        self.assertEqual(gender_tag_from_user("nonbinary"), "gender:non-binary")


class TestOrientationTags(unittest.TestCase):
    def test_user_and_agent_keys(self):
        self.assertEqual(orientation_tag("heterosexual"), "orientation:heterosexual")
        self.assertEqual(orientation_tag("双性恋"), "orientation:bisexual")
        self.assertEqual(orientation_tag("secret"), "orientation:secret")
        self.assertIsNone(orientation_tag(""))
        self.assertIsNone(orientation_tag("unknown"))

    def test_sync_writes_orientation(self):
        tags = sync_companion_tags("温柔", "女", sexual_orientation="bisexual")
        self.assertEqual(tags, ["gentle", "gender:female", "orientation:bisexual"])

    def test_sync_replaces_orientation(self):
        tags = sync_companion_tags(
            "温柔",
            "女",
            existing=["gentle", "gender:female", "orientation:heterosexual", "romance"],
            sexual_orientation="homosexual",
        )
        self.assertEqual(tags, ["gentle", "gender:female", "orientation:homosexual", "romance"])


class TestPersonalityReverse(unittest.TestCase):
    def test_zh_labels(self):
        keys = keys_from_personality_text("温柔、可爱、重感情")
        self.assertEqual(keys, ["gentle", "cute", "affectionate"])

    def test_en_and_key(self):
        keys = keys_from_personality_text("calm, humorous")
        self.assertEqual(keys, ["calm", "humorous"])

    def test_unresolved(self):
        self.assertEqual(keys_from_personality_text("自定义性格xyz"), [])
        self.assertEqual(keys_from_personality_text(""), [])


class TestSyncCompanionTags(unittest.TestCase):
    def test_null_existing_is_empty(self):
        self.assertEqual(
            sync_companion_tags("温柔", "女", existing=None),
            ["gentle", "gender:female"],
        )
        self.assertEqual(
            sync_companion_tags("温柔", "女", existing=[]),
            ["gentle", "gender:female"],
        )

    def test_unresolved_personality_writes_gender_only(self):
        self.assertEqual(
            sync_companion_tags("自定义性格xyz", "男", existing=None),
            ["gender:male"],
        )

    def test_ops_preserved_gender_and_personality_replaced(self):
        tags = sync_companion_tags(
            "冷静",
            "男",
            existing=["gentle", "gender:female", "romance"],
        )
        self.assertEqual(tags, ["calm", "gender:male", "romance"])

    def test_extra_ops_replaces_ops_only(self):
        tags = sync_companion_tags(
            "温柔",
            "女",
            existing=["romance", "gentle"],
            extra_ops=["custom_ops"],
        )
        self.assertEqual(tags, ["gentle", "gender:female", "custom_ops"])
        self.assertNotIn("romance", tags)

    def test_personality_display_not_used_as_tag(self):
        tags = sync_companion_tags("温柔", "女")
        self.assertNotIn("温柔", tags)
        self.assertNotIn("女", tags)
        self.assertNotIn("男", tags)


class TestUserTagAssemble(unittest.TestCase):
    def test_empty_fields_omitted(self):
        self.assertEqual(assemble_user_tags(ui_lang="zh-CN"), ["lang:zh"])
        self.assertEqual(assemble_user_tags(), [])

    def test_profile_and_ua(self):
        tags = assemble_user_tags(
            chat_purpose="romance",
            preferred_gender="female",
            gender="男",
            sexual_orientation="heterosexual",
            ui_lang="en",
            ops_tags=["vip"],
            ua_tags=["ua:channel:tiktok"],
            mapped_tags=["mapped_key"],
        )
        self.assertIn("purpose:romance", tags)
        self.assertIn("pref_gender:female", tags)
        self.assertIn("gender:male", tags)
        self.assertIn("orientation:heterosexual", tags)
        self.assertIn("lang:en", tags)
        self.assertIn("vip", tags)
        self.assertIn("ua:channel:tiktok", tags)
        self.assertIn("mapped_key", tags)

    def test_null_tags_list(self):
        self.assertEqual(normalize_tag_list(None), [])

    def test_profile_language_not_accept_language(self):
        user = SimpleNamespace(
            chat_purpose="",
            preferred_gender="",
            gender="male",
            sexual_orientation="",
            language="sv",
            tags=[],
            ua_tags=[],
        )
        tags = assemble_user_tags_from_orm(user, ui_lang="en")
        self.assertIn("lang:sv", tags)
        self.assertNotIn("lang:en", tags)

    def test_empty_language_no_lang_tag(self):
        user = SimpleNamespace(
            chat_purpose="",
            preferred_gender="",
            gender="male",
            sexual_orientation="",
            language="",
            tags=[],
            ua_tags=[],
        )
        tags = assemble_user_tags_from_orm(user, ui_lang="sv")
        self.assertNotIn("lang:sv", tags)
        self.assertFalse(any(t.startswith("lang:") for t in tags))

    def test_missing_user_no_lang_from_header(self):
        self.assertEqual(assemble_user_tags_from_orm(None, ui_lang="sv"), [])


class TestGroupMatch(unittest.TestCase):
    def test_and_between_groups_or_inside(self):
        def_map = {
            ("pref_gender:female", "user"): "pref_gender",
            ("pref_gender:male", "user"): "pref_gender",
            ("purpose:romance", "user"): "purpose",
        }
        # 组内 OR
        self.assertTrue(object_matches_tags(
            ["pref_gender:female"],
            ["pref_gender:female", "pref_gender:male"],
            "user",
            def_map,
        ))
        # 组间 AND
        self.assertFalse(object_matches_tags(
            ["pref_gender:female"],
            ["pref_gender:female", "purpose:romance"],
            "user",
            def_map,
        ))
        self.assertTrue(object_matches_tags(
            ["pref_gender:female", "purpose:romance"],
            ["pref_gender:female", "purpose:romance"],
            "user",
            def_map,
        ))
        # 空策略 = 全部
        self.assertTrue(object_matches_tags(["anything"], [], "user", def_map))

    def test_open_gender_does_not_match_gender_strategy(self):
        def_map = {
            ("gender:male", "user"): "gender",
            ("gender:female", "user"): "gender",
            ("lang:sv", "user"): "lang",
            ("ua:source:ads", "user"): "ua",
        }
        self.assertFalse(object_matches_tags(
            ["gender:confidential", "lang:sv", "ua:source:ads"],
            ["gender:male", "ua:source:ads"],
            "user",
            def_map,
        ))
        self.assertFalse(object_matches_tags(
            ["ua:source:ads"],
            ["gender:female", "ua:source:ads"],
            "user",
            def_map,
        ))
        self.assertTrue(object_matches_tags(
            ["gender:female", "ua:source:ads"],
            ["gender:female", "ua:source:ads"],
            "user",
            def_map,
        ))
        self.assertFalse(object_matches_tags(
            ["gender:female", "lang:sv"],
            ["gender:male", "lang:sv"],
            "user",
            def_map,
        ))

    def test_agent_tags_are_contained_order_independent(self):
        from services.recommend_tags import companion_covers_agent_tags

        need = ["gender:male", "femoment", "ops"]
        self.assertTrue(companion_covers_agent_tags(
            ["ops", "femoment", "gender:male", "gentle"],
            need,
        ))
        self.assertFalse(companion_covers_agent_tags(
            ["mild", "gender:female", "femoment", "ops"],
            need,
        ))
        self.assertFalse(companion_covers_agent_tags(
            ["gender:male", "ops", "mmoment"],
            need,
        ))
        def_map = {
            ("ops", "agent"): "ops",
            ("temprecompanis", "agent"): "ops",
            ("gender:male", "agent"): "gender",
        }
        self.assertTrue(object_matches_tags(
            ["gender:male", "ops", "temprecompanis"],
            ["ops", "temprecompanis"],
            "agent",
            def_map,
        ))
        self.assertFalse(object_matches_tags(
            ["gender:male", "ops"],
            ["ops", "temprecompanis"],
            "agent",
            def_map,
        ))
        self.assertTrue(object_matches_tags(
            ["gender:male", "ops"],
            ["gender:male", "ops"],
            "agent",
            def_map,
        ))


if __name__ == "__main__":
    unittest.main()
