"""推荐瀑布：入口矩阵、priority、空池、apply_to、全站截胡、杀开关、硬筛、count/feed 同池。"""
from __future__ import annotations

import os
import sys
import unittest

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.ad_attribution import GENERIC_AD_USER_TAG
from services.recommend import (
    flatten_grouped_selection as flatten_sel,
    interleave_companion_rounds,
    resolve_waterfall,
    should_apply_waterfall,
    should_pad_recommend_pool,
    validate_strategy_tags,
    window_for_strategy_then_fill,
    window_for_three_tiers,
)


class TestEntryMatrix(unittest.TestCase):
    def test_recommend_and_all_use_waterfall(self):
        self.assertTrue(should_apply_waterfall(special="recommend"))
        self.assertTrue(should_apply_waterfall(special="recommend", intimacy_value=5))
        self.assertTrue(should_apply_waterfall(special=None, intimacy_value=0, min_turns=0))

    def test_message_and_mine_never_use_waterfall(self):
        self.assertFalse(should_apply_waterfall())
        self.assertFalse(should_apply_waterfall(special="message"))
        self.assertFalse(should_apply_waterfall(special="message", intimacy_value=0))
        self.assertFalse(should_apply_waterfall(is_created=True))
        self.assertFalse(should_apply_waterfall(is_created=True, intimacy_value=0))
        self.assertFalse(should_apply_waterfall(intimacy_value=5))
        self.assertFalse(should_apply_waterfall(min_turns=1))
        self.assertFalse(should_apply_waterfall(is_created=False))
        self.assertFalse(should_apply_waterfall(intimacy_value=0, intimacy_gt=True))


class TestDeviceLangFilter(unittest.TestCase):
    def test_accept_language_normalizes(self):
        from services.recommend import device_lang_filter

        self.assertEqual(device_lang_filter("", "en"), "en")
        self.assertEqual(device_lang_filter("", "en-US"), "en")
        self.assertEqual(device_lang_filter("ja", "en"), "ja")
        self.assertEqual(device_lang_filter("", ""), "")

    def test_user_lang_then_accept_language(self):
        from services.recommend import resolve_companion_list_lang

        class _Q:
            def filter(self, *a, **k):
                return self

            def first(self):
                return self.user

        class _DB:
            def __init__(self, user):
                self._q = _Q()
                self._q.user = user

            def query(self, *a, **k):
                return self._q

        user = type("U", (), {"language": "ja"})()
        self.assertEqual(
            resolve_companion_list_lang(
                _DB(user), 1, filter_lang="", accept_lang="en"
            ),
            "ja",
        )
        empty = type("U", (), {"language": ""})()
        self.assertEqual(
            resolve_companion_list_lang(
                _DB(empty), 1, filter_lang="", accept_lang="en-US"
            ),
            "en",
        )
        self.assertEqual(
            resolve_companion_list_lang(
                _DB(user), 1, filter_lang="zh", accept_lang="en"
            ),
            "zh",
        )

    def test_waterfall_uses_user_then_accept_lang(self):
        path = os.path.join(os.path.dirname(__file__), "..", "services", "companion_manager.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def _apply_list_waterfall")
        block = src[start : src.index("def _list_other_scenes")]
        self.assertIn("resolve_companion_list_lang", block)
        self.assertIn("accept_lang=lang", block)
        self.assertIn("hard_lang", block)

    def test_message_scene_has_no_lang_hard_filter(self):
        path = os.path.join(os.path.dirname(__file__), "..", "services", "companion_manager.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def _list_message_scene")
        end = src.find("\n    def ", start + 10)
        block = src[start:end] if end > 0 else src[start:]
        self.assertNotIn("device_lang_filter", block)
        self.assertNotIn("resolve_companion_list_lang", block)
        self.assertNotIn("apply_hard_filters", block)


class TestRecommendPopularFill(unittest.TestCase):
    def test_pad_when_empty_or_short(self):
        self.assertTrue(should_pad_recommend_pool(0, 20))
        self.assertTrue(should_pad_recommend_pool(5, 20))
        self.assertTrue(should_pad_recommend_pool(5, 5))
        self.assertTrue(should_pad_recommend_pool(10, 20))
        self.assertFalse(should_pad_recommend_pool(20, 20))
        self.assertFalse(should_pad_recommend_pool(25, 20))

    def test_matched_strategy_never_pads(self):
        self.assertFalse(should_pad_recommend_pool(0, 20, strategy_matched=True))
        self.assertFalse(should_pad_recommend_pool(5, 20, strategy_matched=True))

    def test_moments_lang_fill_off_when_strategy_matched(self):
        from services.recommend import should_fill_moments_lang_tiers

        self.assertFalse(should_fill_moments_lang_tiers(strategy_matched=True))
        self.assertTrue(should_fill_moments_lang_tiers(strategy_matched=False))

    def test_window_page1_keeps_strategy_then_fill(self):
        s_off, s_lim, f_off, f_lim, total = window_for_strategy_then_fill(5, 100, 0, 20)
        self.assertEqual((s_off, s_lim, f_off, f_lim, total), (0, 5, 0, 15, 105))

    def test_window_page2_continues_fill(self):
        s_off, s_lim, f_off, f_lim, total = window_for_strategy_then_fill(5, 100, 20, 20)
        self.assertEqual((s_off, s_lim, f_off, f_lim, total), (0, 0, 15, 20, 105))

    def test_window_empty_strategy_is_all_fill(self):
        s_off, s_lim, f_off, f_lim, total = window_for_strategy_then_fill(0, 40, 0, 20)
        self.assertEqual((s_off, s_lim, f_off, f_lim, total), (0, 0, 0, 20, 40))

    def test_three_tiers_primary_then_same_then_other(self):
        # 主池 3 + 同语言 4 + 其他语言 10；page_size=5
        o1, l1, o2, l2, o3, l3, total = window_for_three_tiers(3, 4, 10, 0, 5)
        self.assertEqual(total, 17)
        self.assertEqual((o1, l1), (0, 3))
        self.assertEqual((o2, l2), (0, 2))
        self.assertEqual((o3, l3), (0, 0))

        o1, l1, o2, l2, o3, l3, total = window_for_three_tiers(3, 4, 10, 5, 5)
        self.assertEqual((o1, l1), (0, 0))
        self.assertEqual((o2, l2), (2, 2))
        self.assertEqual((o3, l3), (0, 3))

        o1, l1, o2, l2, o3, l3, total = window_for_three_tiers(3, 4, 10, 10, 5)
        self.assertEqual((o1, l1), (0, 0))
        self.assertEqual((o2, l2), (0, 0))
        self.assertEqual((o3, l3), (3, 5))  # 越过主池3+同语言4后，其他语言从 offset 3 起


class TestInterleaveCompanionRounds(unittest.TestCase):
    def test_interleave_within_tier_then_concat(self):
        primary = [("a1", "A"), ("a2", "A"), ("a3", "A"), ("a4", "A"), ("d1", "D")]
        same = [("b1", "B"), ("b2", "B")]
        other = [("c1", "C")]
        out = (
            interleave_companion_rounds(primary)
            + interleave_companion_rounds(same)
            + interleave_companion_rounds(other)
        )
        self.assertEqual(out[:5], ["a1", "d1", "a2", "a3", "a4"])
        self.assertEqual(out[5:], ["b1", "b2", "c1"])
        self.assertEqual(len(out), 8)

    def test_preserves_within_companion_order(self):
        primary = [("a2", "A"), ("a1", "A")]
        out = interleave_companion_rounds(primary, [], [])
        self.assertEqual(out, ["a2", "a1"])

    def test_empty_tiers(self):
        self.assertEqual(interleave_companion_rounds([], [], []), [])
        self.assertEqual(interleave_companion_rounds([("x1", "X")]), ["x1"])


class _FakeQuery:
    """记录硬筛条件，不连库。"""
    def __init__(self):
        self.filters = []

    def filter(self, *args):
        self.filters.extend(args)
        return self


class TestWaterfallResolve(unittest.TestCase):
    def setUp(self):
        self.def_map = {
            ("ua:channel:tiktok", "user"): "ua",
            (GENERIC_AD_USER_TAG, "user"): "ua",
            ("gender:female", "user"): "gender",
            ("gender:male", "user"): "gender",
            ("lang:sv", "user"): "lang",
            ("lang:sw", "user"): "lang",
            ("lang:zh", "user"): "lang",
            ("purpose:romance", "user"): "purpose",
            ("pref_gender:female", "user"): "pref_gender",
            ("cute", "agent"): "personality",
            ("gentle", "agent"): "personality",
            ("gender:female", "agent"): "gender",
            ("orientation:pansexual", "agent"): "orientation",
            ("orientation:homosexual", "agent"): "orientation",
            ("orientation:bisexual", "agent"): "orientation",
            ("affectionate", "agent"): "personality",
            ("artsy", "agent"): "personality",
            ("romance", "agent"): "ops",
        }
        self.s_tiktok_cute = {
            "id": 1, "enabled": True, "priority": 100, "apply_to": "both",
            "user_tags": ["ua:channel:tiktok"], "agent_tags": ["cute", "gentle"],
            "user_match": "any", "agent_match": "any",
        }
        self.s_global_romance = {
            "id": 2, "enabled": True, "priority": 20, "apply_to": "both",
            "user_tags": [GENERIC_AD_USER_TAG], "agent_tags": ["romance"],
            "user_match": "any", "agent_match": "any",
        }
        self.s_moments_only = {
            "id": 3, "enabled": True, "priority": 200, "apply_to": "moments",
            "user_tags": ["ua:channel:tiktok"], "agent_tags": ["cute"],
            "user_match": "any", "agent_match": "any",
        }
        self.s_recommend_only = {
            "id": 4, "enabled": True, "priority": 80, "apply_to": "companions_recommend",
            "user_tags": [GENERIC_AD_USER_TAG], "agent_tags": ["gender:female"],
            "user_match": "any", "agent_match": "any",
        }
        self.s_all_only = {
            "id": 5, "enabled": True, "priority": 80, "apply_to": "companions_all",
            "user_tags": [GENERIC_AD_USER_TAG], "agent_tags": ["cute"],
            "user_match": "any", "agent_match": "any",
        }
        self.s_gender_only_no_ad = {
            "id": 6, "enabled": True, "priority": 90, "apply_to": "both",
            "user_tags": ["gender:female"], "agent_tags": ["romance"],
            "user_match": "any", "agent_match": "any",
        }
        self.s_lang_and_gender = {
            "id": 9, "enabled": True, "priority": 0, "apply_to": "companions_recommend",
            "user_tags": ["gender:male", "lang:sv"], "agent_tags": ["gender:female"],
            "user_match": "any", "agent_match": "any",
        }
        self.s_ads_sw_male = {
            "id": 8, "enabled": True, "priority": 99, "apply_to": "both",
            "user_tags": ["gender:male", "lang:sw", "ua:source:ads"],
            "agent_tags": [
                "gender:female", "romance", "test",
                "orientation:pansexual", "orientation:homosexual", "orientation:bisexual",
                "affectionate", "artsy",
            ],
            "user_match": "any", "agent_match": "any",
        }

    def _count(self, pools):
        def fn(agent_tags, agent_match, lang_eq):
            key = (tuple(agent_tags or []), lang_eq)
            return int(pools.get(key, 0))
        return fn

    def test_priority_and_user_hit(self):
        pools = {(("cute", "gentle"), None): 3}
        r = resolve_waterfall(
            surface="companions",
            user_tags=["ua:channel:tiktok", "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_global_romance, self.s_tiktok_cute],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "strategy")
        self.assertEqual(r.matched_strategy_id, 1)
        self.assertEqual(r.fallback_layer, "strategy")

    def test_empty_agent_pool_does_not_change_strategy(self):
        """用户已命中更具体策略时，智能体池空也不得改选下一条。"""
        pools = {(("romance",), None): 2}
        r = resolve_waterfall(
            surface="companions",
            user_tags=["ua:channel:tiktok", "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_tiktok_cute, self.s_global_romance],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.matched_strategy_id, 1)
        self.assertEqual(r.agent_tags, ["cute", "gentle"])

    def test_strategy_without_ad_audience_matches_organic(self):
        """未选广义/精准：仅自然量；带广告标识的用户不命中。"""
        pools = {(("romance",), None): 5, ((), "zh"): 1}
        r = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:female", "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_gender_only_no_ad],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "strategy")
        self.assertEqual(r.matched_strategy_id, 6)
        ad_user = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:female", GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_gender_only_no_ad],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(ad_user.layer, "empty")

    def test_empty_user_tags_matches_organic_only(self):
        s = {
            "id": 8, "enabled": True, "priority": 50, "apply_to": "both",
            "user_tags": [], "agent_tags": ["romance"],
            "user_match": "any", "agent_match": "any",
        }
        pools = {(("romance",), None): 2}
        organic = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["lang:zh"],
            ui_lang="zh",
            strategies=[s],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(organic.matched_strategy_id, 8)
        ad = resolve_waterfall(
            surface="companions_recommend",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[s],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(ad.layer, "empty")

    def test_generic_and_specific_ad_union(self):
        s = {
            "id": 9, "enabled": True, "priority": 80, "apply_to": "both",
            "user_tags": [GENERIC_AD_USER_TAG, "ua:campaign:123"],
            "agent_tags": ["romance"],
            "user_match": "any", "agent_match": "any",
        }
        pools = {(("romance",), None): 1}
        hit_generic = resolve_waterfall(
            surface="companions_recommend",
            user_tags=[GENERIC_AD_USER_TAG],
            ui_lang="zh",
            strategies=[s],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        hit_specific = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["ua:campaign:123"],
            ui_lang="zh",
            strategies=[s],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        miss_organic = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["lang:zh"],
            ui_lang="zh",
            strategies=[s],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(hit_generic.matched_strategy_id, 9)
        self.assertEqual(hit_specific.matched_strategy_id, 9)
        self.assertEqual(miss_organic.layer, "empty")

    def test_gender_plus_generic_ad_matches_ad_female(self):
        s = {
            "id": 7, "enabled": True, "priority": 99, "apply_to": "both",
            "user_tags": ["gender:female", GENERIC_AD_USER_TAG],
            "agent_tags": ["romance"],
            "user_match": "any", "agent_match": "any",
        }
        pools = {(("romance",), None): 3}
        hit = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:female", GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[s],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(hit.matched_strategy_id, 7)
        miss = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:female", "lang:zh"],
            ui_lang="zh",
            strategies=[s],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(miss.layer, "empty")

    def test_apply_to_skips_other_surface(self):
        pools = {(("cute",), None): 9, ((), "zh"): 1}
        r = resolve_waterfall(
            surface="companions",
            user_tags=["ua:channel:tiktok"],
            ui_lang="zh",
            strategies=[self.s_moments_only],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "all")
        self.assertIsNone(r.matched_strategy_id)

    def test_recommend_and_all_are_separate_scenes(self):
        pools = {(("gender:female",), None): 4, (("cute",), None): 9, ((), "zh"): 1}
        r_rec = resolve_waterfall(
            surface="companions_recommend",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_recommend_only, self.s_all_only],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r_rec.matched_strategy_id, 4)
        r_all = resolve_waterfall(
            surface="companions_all",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_recommend_only, self.s_all_only],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r_all.matched_strategy_id, 5)

    def test_higher_priority_wins_when_two_match(self):
        pools = {(("cute", "gentle"), None): 3, (("romance",), None): 4}
        r = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["ua:channel:tiktok", "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_global_romance, self.s_tiktok_cute],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.matched_strategy_id, 1)
        self.assertEqual(r.matched_strategy_ids, [1])
        self.assertEqual(r.agent_tags, ["cute", "gentle"])

    def test_recommend_no_hit_returns_empty(self):
        r = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["lang:zh"],
            ui_lang="zh",
            strategies=[self.s_tiktok_cute],
            count_fn=self._count({((), "zh"): 5}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "empty")
        self.assertFalse(r.popularity_fallback)
        self.assertIsNone(r.matched_strategy_id)

    def test_recommend_requires_profile_lang_and_gender(self):
        pools = {(("gender:female",), None): 4}
        hit = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:male", "lang:sv"],
            ui_lang="en",
            strategies=[self.s_lang_and_gender],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(hit.layer, "strategy")
        self.assertEqual(hit.matched_strategy_id, 9)

        no_lang = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:male"],
            ui_lang="sv",
            strategies=[self.s_lang_and_gender],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(no_lang.layer, "empty")

        wrong_lang = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:male", "lang:en"],
            ui_lang="sv",
            strategies=[self.s_lang_and_gender],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(wrong_lang.layer, "empty")

    def test_ads_sw_male_requires_user_and_agent_rules(self):
        agent_key = (
            "gender:female", "romance", "test",
            "orientation:pansexual", "orientation:homosexual", "orientation:bisexual",
            "affectionate", "artsy",
        )
        pools = {(agent_key, None): 6}
        hit = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:male", "lang:sw", GENERIC_AD_USER_TAG],
            ui_lang="en",
            strategies=[self.s_ads_sw_male],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(hit.layer, "strategy")
        self.assertEqual(hit.matched_strategy_id, 8)
        self.assertEqual(hit.agent_tags, list(agent_key))

        no_ads = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:male", "lang:sw"],
            ui_lang="en",
            strategies=[self.s_ads_sw_male],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(no_ads.layer, "empty")

        no_lang = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:male", GENERIC_AD_USER_TAG],
            ui_lang="en",
            strategies=[self.s_ads_sw_male],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(no_lang.layer, "empty")

        female_user = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:female", "lang:sw", GENERIC_AD_USER_TAG],
            ui_lang="en",
            strategies=[self.s_ads_sw_male],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(female_user.layer, "empty")

    def test_confidential_user_skips_gender_strategy(self):
        pools = {(("gender:female",), None): 4}
        hit = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:confidential", "lang:sv"],
            ui_lang="en",
            strategies=[self.s_lang_and_gender],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(hit.layer, "empty")
        self.assertIsNone(hit.matched_strategy_id)

    def test_no_gender_ad_user_hits_temp_not_gendered(self):
        def_map = dict(self.def_map)
        def_map.update({
            ("ops", "agent"): "ops",
            ("ferecompanis", "agent"): "ops",
            ("merecompanis", "agent"): "ops",
            ("temprecompanis", "agent"): "ops",
            ("adsa", "agent"): "ops",
            ("gender:male", "agent"): "gender",
        })
        s20 = {
            "id": 20, "enabled": True, "priority": 100,
            "apply_to": "companions_recommend",
            "user_tags": ["gender:female", GENERIC_AD_USER_TAG],
            "agent_tags": ["gender:male", "ops", "ferecompanis"],
            "user_match": "any", "agent_match": "any",
        }
        s21 = {
            "id": 21, "enabled": True, "priority": 100,
            "apply_to": "companions_recommend",
            "user_tags": ["gender:male", GENERIC_AD_USER_TAG],
            "agent_tags": ["gender:female", "merecompanis", "ops"],
            "user_match": "any", "agent_match": "any",
        }
        s16 = {
            "id": 16, "enabled": True, "priority": 99,
            "apply_to": "companions_recommend",
            "user_tags": [GENERIC_AD_USER_TAG],
            "agent_tags": ["ops", "temprecompanis"],
            "user_match": "any", "agent_match": "any",
        }
        s24 = {
            "id": 24, "enabled": True, "priority": 98,
            "apply_to": "both",
            "user_tags": [GENERIC_AD_USER_TAG],
            "agent_tags": ["ops", "adsa"],
            "user_match": "any", "agent_match": "any",
        }
        pools = {
            (("gender:male", "ops", "ferecompanis"), None): 1,
            (("gender:female", "merecompanis", "ops"), None): 1,
            (("ops", "temprecompanis"), None): 2,
            (("ops", "adsa"), None): 1,
        }
        strategies = [s20, s21, s16, s24]
        no_gender = resolve_waterfall(
            surface="companions_recommend",
            user_tags=[GENERIC_AD_USER_TAG, "lang:en"],
            ui_lang="en",
            strategies=strategies,
            count_fn=self._count(pools),
            tag_def_map=def_map,
        )
        self.assertEqual(no_gender.matched_strategy_id, 16)
        confidential = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:confidential", GENERIC_AD_USER_TAG, "lang:en"],
            ui_lang="en",
            strategies=strategies,
            count_fn=self._count(pools),
            tag_def_map=def_map,
        )
        self.assertEqual(confidential.matched_strategy_id, 16)
        female = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:female", GENERIC_AD_USER_TAG, "lang:en"],
            ui_lang="en",
            strategies=strategies,
            count_fn=self._count(pools),
            tag_def_map=def_map,
        )
        self.assertEqual(female.matched_strategy_id, 20)
        male = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:male", GENERIC_AD_USER_TAG, "lang:en"],
            ui_lang="en",
            strategies=strategies,
            count_fn=self._count(pools),
            tag_def_map=def_map,
        )
        self.assertEqual(male.matched_strategy_id, 21)

    def test_gender_ads_empty_pool_does_not_fall_to_temp(self):
        """女广告户已命中性别+广告，智能体池空也不用仅 ads 的临时策略。"""
        def_map = dict(self.def_map)
        def_map.update({
            ("ops", "agent"): "ops",
            ("ferecompanis", "agent"): "ops",
            ("temprecompanis", "agent"): "ops",
            ("gender:male", "agent"): "gender",
        })
        s20 = {
            "id": 20, "enabled": True, "priority": 100,
            "apply_to": "companions_recommend",
            "user_tags": ["gender:female", GENERIC_AD_USER_TAG],
            "agent_tags": ["gender:male", "ops", "ferecompanis"],
            "user_match": "any", "agent_match": "any",
        }
        s16 = {
            "id": 16, "enabled": True, "priority": 99,
            "apply_to": "companions_recommend",
            "user_tags": [GENERIC_AD_USER_TAG],
            "agent_tags": ["ops", "temprecompanis"],
            "user_match": "any", "agent_match": "any",
        }
        pools = {
            (("gender:male", "ops", "ferecompanis"), None): 0,
            (("ops", "temprecompanis"), None): 2,
        }
        r = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["gender:female", GENERIC_AD_USER_TAG, "lang:en"],
            ui_lang="en",
            strategies=[s20, s16],
            count_fn=self._count(pools),
            tag_def_map=def_map,
        )
        self.assertEqual(r.matched_strategy_id, 20)
        self.assertEqual(r.layer, "strategy")
        self.assertEqual(r.agent_tags, ["gender:male", "ops", "ferecompanis"])

    def test_legacy_companions_covers_both_list_scenes(self):
        s = dict(self.s_all_only, apply_to="companions", agent_tags=["cute"])
        pools = {(("cute",), None): 3}
        for surface in ("companions_all", "companions_recommend", "companions"):
            r = resolve_waterfall(
                surface=surface,
                user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
                ui_lang="zh",
                strategies=[s],
                count_fn=self._count(pools),
                tag_def_map=self.def_map,
            )
            self.assertEqual(r.layer, "strategy", surface)

    def test_global_skips_language_layer(self):
        pools = {(("romance",), None): 4, ((), "zh"): 10}
        r = resolve_waterfall(
            surface="companions",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_global_romance],
            count_fn=self._count(pools),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "strategy")
        self.assertFalse(r.is_global)
        self.assertEqual(r.fallback_layer, "strategy")

    def test_kill_switch_all_with_soft_lang(self):
        """无启用策略 → 全量；同语种池非空也不硬筛掉其它语种。"""
        r = resolve_waterfall(
            surface="companions",
            user_tags=["lang:zh"],
            ui_lang="zh",
            strategies=[],
            count_fn=self._count({((), "zh"): 2}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "all")
        self.assertEqual(r.fallback_layer, "all")
        self.assertIsNone(r.lang_eq)

        r2 = resolve_waterfall(
            surface="companions",
            user_tags=["lang:ja"],
            ui_lang="ja",
            strategies=[],
            count_fn=self._count({}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r2.layer, "all")
        self.assertEqual(r2.fallback_layer, "all")

    def test_new_user_no_global_goes_language(self):
        r = resolve_waterfall(
            surface="companions",
            user_tags=["lang:zh"],
            ui_lang="zh",
            strategies=[self.s_tiktok_cute],
            count_fn=self._count({((), "zh"): 5}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "language")

    def test_logged_out_skips_strategy(self):
        r = resolve_waterfall(
            surface="moments",
            user_tags=["lang:zh"],
            ui_lang="zh",
            strategies=[self.s_tiktok_cute],
            count_fn=self._count({(("cute", "gentle"), None): 8, ((), "zh"): 3}),
            logged_in=False,
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "language")

    def test_disabled_strategies_are_kill_switch(self):
        s = dict(self.s_tiktok_cute, enabled=False)
        r = resolve_waterfall(
            surface="companions",
            user_tags=["ua:channel:tiktok"],
            ui_lang="en",
            strategies=[s],
            count_fn=self._count({((), "en"): 1}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "all")
        self.assertIsNone(r.lang_eq)


class TestCollectUserTagsLoggedOut(unittest.TestCase):
    def test_logged_out_empty_set(self):
        from services.recommend import collect_user_tags

        self.assertEqual(collect_user_tags(None, None, "zh"), [])


class TestBothEmptyAndGroups(unittest.TestCase):
    def test_both_empty_rejected(self):
        self.assertEqual(validate_strategy_tags([], [], tag_defs={}), "both_sides_empty")
        defs = {("cute", "agent"): {"group": "personality"}}
        # 广告受众可空：仅智能体侧有标签即可保存
        self.assertEqual(validate_strategy_tags([], ["cute"], tag_defs=defs), None)
        self.assertEqual(
            validate_strategy_tags([GENERIC_AD_USER_TAG], ["cute"], tag_defs=defs),
            None,
        )
        self.assertTrue(
            validate_strategy_tags(["nope"], ["cute"], tag_defs=defs).startswith("unknown_user_tag")
        )

    def test_flatten_all_is_empty_array(self):
        self.assertEqual(flatten_sel({"purpose": ["__all__"], "gender": []}), [])
        self.assertEqual(flatten_sel({"purpose": ["purpose:romance"], "gender": ["__all__"]}), ["purpose:romance"])


class TestHardFilterNoDowngrade(unittest.TestCase):
    def test_hard_filter_is_separate_from_resolve(self):
        """硬筛叠在最终池上：resolve 不看 hard filter。"""
        def_map = {
            ("cute", "agent"): "personality",
            (GENERIC_AD_USER_TAG, "user"): "ua",
        }
        s = {
            "id": 9, "enabled": True, "priority": 1, "apply_to": "both",
            "user_tags": [GENERIC_AD_USER_TAG], "agent_tags": ["cute"],
            "user_match": "any", "agent_match": "any",
        }
        r = resolve_waterfall(
            surface="companions",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[s],
            count_fn=lambda agent_tags, m, lang: 4 if agent_tags == ["cute"] else 0,
            tag_def_map=def_map,
        )
        self.assertEqual(r.layer, "strategy")
        # 硬筛由调用方叠在 query 上，滤空不降级——这里只断言 resolve 已锁定该档
        self.assertEqual(r.matched_strategy_id, 9)


class TestCountFeedSamePool(unittest.TestCase):
    def test_same_resolve_inputs_same_layer(self):
        """count 与 feed 必须共用 resolve + 同一套池条件。"""
        def_map = {
            ("romance", "agent"): "ops",
            (GENERIC_AD_USER_TAG, "user"): "ua",
        }
        s = {
            "id": 5, "enabled": True, "priority": 1, "apply_to": "moments",
            "user_tags": [GENERIC_AD_USER_TAG], "agent_tags": ["romance"],
            "user_match": "any", "agent_match": "any",
        }
        count_fn = lambda agent_tags, m, lang: 7 if agent_tags == ["romance"] else 0
        kwargs = dict(
            surface="moments",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[s],
            count_fn=count_fn,
            tag_def_map=def_map,
        )
        a = resolve_waterfall(**kwargs)
        b = resolve_waterfall(**kwargs)
        self.assertEqual(a.layer, b.layer)
        self.assertEqual(a.agent_tags, b.agent_tags)
        self.assertEqual(a.matched_strategy_id, b.matched_strategy_id)


if __name__ == "__main__":
    unittest.main()
