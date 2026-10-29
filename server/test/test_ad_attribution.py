"""Ad attribution tag helpers."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ad_attribution import (
    GENERIC_AD_USER_TAG,
    enrich_paid_ad_tags,
    is_ad_user_tag,
    is_qualified_ad_install,
    is_specific_ad_tag,
    split_ad_and_other_user_tags,
    split_strategy_ad_tags,
    tags_from_attribution_fields,
)
from services.growth_attribution import extract_gclid_and_campaign
from services.recommend import resolve_waterfall


class TestExtractClickIds(unittest.TestCase):
    def test_parse_play_referrer_with_top_gclid(self):
        """顶层已有 gclid 时仍要从 play_referrer 抽出 gad_campaignid。"""
        gclid, camp = extract_gclid_and_campaign(
            raw_payload={
                "gclid": "CjwKCAjwy5rUBhB5EiwAIoAtCjyuHc2KVHdPdr12BlS2LWlAfTqiFBWBYD3uM8ZzRDx-pkHqP6DYOxoCrXIQAvD_BwE",
                "play_referrer": (
                    "gclid=CjwKCAjwy5rUBhB5EiwAIoAtCjyuHc2KVHdPdr12BlS2LWlAfTqiFBWBYD3uM8ZzRDx-pkHqP6DYOxoCrXIQAvD_BwE"
                    "&gbraid=0AAAABEaB6X3_tkbZjbC0YT6lAlOgsNTLD"
                    "&gad_source=5&gad_campaignid=24151095811"
                ),
            }
        )
        self.assertTrue(gclid.startswith("CjwKCAjwy5rU"))
        self.assertEqual(camp, "24151095811")

    def test_top_level_wins(self):
        gclid, camp = extract_gclid_and_campaign(
            gclid="TOP_GCLID",
            gad_campaignid="TOP_CAMP",
            raw_payload={"play_referrer": "gclid=OTHER&gad_campaignid=999"},
        )
        self.assertEqual(gclid, "TOP_GCLID")
        self.assertEqual(camp, "TOP_CAMP")


class TestAdAttribution(unittest.TestCase):
    def test_install_ads_enough_for_generic(self):
        self.assertTrue(
            is_qualified_ad_install(event_type="install", install_source="ads")
        )
        self.assertFalse(
            is_qualified_ad_install(event_type="login", install_source="ads", gclid="x")
        )

    def test_tags_install_ads_only(self):
        tags = tags_from_attribution_fields(
            event_type="install",
            install_source="ads",
        )
        self.assertEqual(tags, [GENERIC_AD_USER_TAG])

    def test_tags_with_gclid_and_campaign_from_raw(self):
        tags = tags_from_attribution_fields(
            event_type="install",
            install_source="ads",
            raw_payload={
                "gclid": "CjwKC_TEST",
                "play_referrer": "gclid=CjwKC_TEST&gad_campaignid=24151095811",
            },
        )
        self.assertIn(GENERIC_AD_USER_TAG, tags)
        self.assertIn("ua:campaign:24151095811", tags)
        self.assertIn("ua:gclid:CjwKC_TEST", tags)

    def test_login_not_tagged(self):
        tags = tags_from_attribution_fields(
            event_type="login",
            install_source="ads",
            gclid="x",
            gad_campaignid="1",
        )
        self.assertEqual(tags, [])

    def test_strip_not_set(self):
        from services.ad_attribution import _strip_stale_attribution_tags

        cleaned = _strip_stale_attribution_tags(
            [
                "ua:channel:(not set)",
                "ua:medium:(not set)",
                "ua:source:ads",
                "ua:gclid:OLD",
                "ua:campaign:keep_zeas",
            ]
        )
        self.assertEqual(cleaned, ["ua:campaign:keep_zeas"])

    def test_split_strategy_ad_tags(self):
        specific, generic = split_strategy_ad_tags(
            ["ua:campaign:c1", GENERIC_AD_USER_TAG, "purpose:romance"]
        )
        self.assertEqual(specific, ["ua:campaign:c1"])
        self.assertTrue(generic)

    def test_split_ad_tags(self):
        ad, other = split_ad_and_other_user_tags(
            ["ua:gclid:g1", "purpose:romance", "ua:campaign:c1", GENERIC_AD_USER_TAG]
        )
        self.assertEqual(ad, ["ua:gclid:g1", "ua:campaign:c1", GENERIC_AD_USER_TAG])
        self.assertEqual(other, ["purpose:romance"])
        self.assertTrue(is_specific_ad_tag("ua:gclid:g1"))
        self.assertTrue(is_ad_user_tag(GENERIC_AD_USER_TAG))


class TestAdminAcquisition(unittest.TestCase):
    def test_three_kinds(self):
        from services.ad_attribution import (
            acquisition_from_ua_tags,
            normalize_admin_ua_tags,
        )

        empty = acquisition_from_ua_tags([])
        self.assertEqual(empty["acquisition"], "none")
        self.assertEqual(empty["acquisition_label"], "无")
        self.assertFalse(empty["is_ads"])

        ads = acquisition_from_ua_tags(["ua:source:ads"])
        self.assertEqual(ads["acquisition"], "ads")
        self.assertEqual(ads["acquisition_label"], "广告户")
        self.assertTrue(ads["is_ads"])

        tracked = acquisition_from_ua_tags(
            [
                "ua:source:ads",
                "ua:campaign:24151095811",
                "ua:gclid:EAIaIQobChMI",
            ]
        )
        self.assertEqual(tracked["acquisition"], "attributed")
        self.assertEqual(tracked["acquisition_label"], "带广告")
        self.assertTrue(tracked["is_ads"])

        merged = normalize_admin_ua_tags(["ua:campaign:c1"])
        self.assertIn("ua:source:ads", merged)
        self.assertIn("ua:campaign:c1", merged)
        with self.assertRaises(ValueError):
            normalize_admin_ua_tags(["gender:confidential"])


class TestRecommendAdStrategyTiers(unittest.TestCase):
    def setUp(self):
        self.def_map = {
            ("ua:campaign:c1", "user"): "ua",
            (GENERIC_AD_USER_TAG, "user"): "ua",
            ("cute", "agent"): "personality",
        }
        self.s_specific = {
            "id": 1,
            "enabled": True,
            "priority": 100,
            "apply_to": "companions_recommend",
            "user_tags": ["ua:campaign:c1"],
            "agent_tags": ["cute"],
            "user_match": "any",
            "agent_match": "any",
        }
        self.s_generic = {
            "id": 2,
            "enabled": True,
            "priority": 50,
            "apply_to": "companions_recommend",
            "user_tags": [GENERIC_AD_USER_TAG],
            "agent_tags": ["cute"],
            "user_match": "any",
            "agent_match": "any",
        }

    def _count(self, pools):
        def fn(agent_tags, agent_match, lang_eq):
            key = (tuple(agent_tags or []), lang_eq)
            return int(pools.get(key, 0))
        return fn

    def test_generic_matches_ads_user(self):
        r = resolve_waterfall(
            surface="companions_recommend",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_generic],
            count_fn=self._count({(("cute",), None): 3}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.matched_strategy_id, 2)

    def test_specific_only_ignores_generic_user(self):
        r = resolve_waterfall(
            surface="companions_recommend",
            user_tags=[GENERIC_AD_USER_TAG, "lang:zh"],
            ui_lang="zh",
            strategies=[self.s_specific],
            count_fn=self._count({(("cute",), None): 3}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "empty")

    def test_organic_user_gets_empty(self):
        r = resolve_waterfall(
            surface="companions_recommend",
            user_tags=["lang:zh"],
            ui_lang="zh",
            strategies=[self.s_specific, self.s_generic],
            count_fn=self._count({(("cute",), None): 3}),
            tag_def_map=self.def_map,
        )
        self.assertEqual(r.layer, "empty")


if __name__ == "__main__":
    unittest.main()
