"""消息策略库：随机 1～3 智能体 + 事件 companion_id 优先。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

import services.growth_attribution as ga  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from services.msg_strategy_crud import library_to_dict  # noqa: E402
from services.msg_strategy_engine import (  # noqa: E402
    COMPANION_ID_PRIORITY_EVENTS,
    _companion_matches_user_lang,
    _library_winners,
    _resolve_dispatch_language,
    companion_id_priority_applies,
    filter_library_rows_by_locale,
    is_companion_bound_event,
    library_companion_pick_count,
)


class TestExtractEventCompanionId(unittest.TestCase):
    def test_top_level_companion_id(self):
        cid = ga.extract_event_companion_id({"companion_id": "c42"})
        self.assertEqual(cid, "c42")

    def test_firebase_params_companion_id(self):
        cid = ga.extract_event_companion_id(
            {},
            firebase_event_params={"companion_id": "c99"},
        )
        self.assertEqual(cid, "c99")

    def test_raw_payload_companion_id(self):
        cid = ga.extract_event_companion_id(
            {},
            raw_payload={"companion_id": "c01"},
        )
        self.assertEqual(cid, "c01")

    def test_priority_top_level_over_firebase(self):
        cid = ga.extract_event_companion_id(
            {"companion_id": "TOP"},
            firebase_event_params={"companion_id": "FB"},
        )
        self.assertEqual(cid, "TOP")

    def test_firebase_companion_id_camel_case(self):
        cid = ga.extract_event_companion_id(
            {},
            firebase_event_params={"companionId": "c77"},
        )
        self.assertEqual(cid, "c77")

    def test_missing_companion_id_is_empty(self):
        self.assertEqual(ga.extract_event_companion_id({}), "")
        self.assertEqual(ga.extract_event_companion_id({"companion_id": None}), "")
        self.assertEqual(ga.extract_event_companion_id(None), "")

    def test_top_level_moment_id(self):
        self.assertEqual(ga.extract_event_moment_id({"moment_id": 88}), 88)

    def test_firebase_target_id_as_moment(self):
        self.assertEqual(
            ga.extract_event_moment_id({}, firebase_event_params={"target_id": "91"}),
            91,
        )

    def test_missing_moment_id_is_none(self):
        self.assertIsNone(ga.extract_event_moment_id({}))
        self.assertIsNone(ga.extract_event_moment_id({"moment_id": ""}))

    def test_build_attribution_without_companion_id(self):
        req = MagicMock()
        req.client = None
        req.headers = {}
        fields = ga.build_attribution_fields(
            req,
            {
                "event_type": "product",
                "device_id": "dev-1",
                "product_event": "home_page_show",
            },
        )
        self.assertEqual(fields.get("companion_id"), "")

    def test_build_attribution_includes_companion_id(self):
        req = MagicMock()
        req.client = None
        req.headers = {}
        fields = ga.build_attribution_fields(
            req,
            {
                "event_type": "product",
                "device_id": "dev-1",
                "product_event": "chat_detail_click",
                "companion_id": "c42",
            },
        )
        self.assertEqual(fields["companion_id"], "c42")


class TestCompanionIdPriority(unittest.TestCase):
    def test_priority_events_set(self):
        for key in (
            "moments_comment_click",
            "free_message_pay_popup",
            "moments_detail_click",
            "avatar_profile_click",
            "profile_displayed_15s",
            "Moment_15s",
            "send_message_click_moment",
            "send_message_click_im",
            "chat_detail_click",
            "send_message_icon_click",
            "chat_page_view",
            "say_hi_pay_popup",
            "dree_message_3",
            "dree_message_5",
        ):
            self.assertIn(key, COMPANION_ID_PRIORITY_EVENTS)
            self.assertTrue(is_companion_bound_event(key))
        self.assertNotIn("home_page_show", COMPANION_ID_PRIORITY_EVENTS)
        self.assertNotIn("feedback_submit_click", COMPANION_ID_PRIORITY_EVENTS)
        self.assertFalse(is_companion_bound_event("feedback_submit_click"))

    def test_applies_when_event_and_companion_present(self):
        self.assertTrue(companion_id_priority_applies("chat_detail_click", "c42"))
        self.assertFalse(companion_id_priority_applies("feedback_submit_click", "c42"))
        self.assertTrue(companion_id_priority_applies("moments_comment_click", "c01"))

    def test_not_applies_without_companion(self):
        self.assertFalse(companion_id_priority_applies("chat_detail_click", ""))
        self.assertTrue(is_companion_bound_event("chat_detail_click"))

    def test_not_applies_for_other_events(self):
        self.assertFalse(companion_id_priority_applies("home_page_show", "c42"))


class TestLibraryLocaleFilter(unittest.TestCase):
    def _row(self, language: str, type_key: str = "send_message_icon_click", row_id: int = 1):
        row = MagicMock()
        row.language = language
        row.type_key = type_key
        row.id = row_id
        return row

    def test_match_user_locale(self):
        rows = [
            self._row("zh", row_id=1),
            self._row("ja", row_id=2),
        ]
        out = filter_library_rows_by_locale(rows, "ja")
        self.assertEqual([r.id for r in out], [2])

    def test_normalize_locale_region(self):
        rows = [self._row("ja", row_id=2), self._row("zh", row_id=1)]
        out = filter_library_rows_by_locale(rows, "ja-JP")
        self.assertEqual([r.id for r in out], [2])

    def test_fallback_en_when_locale_missing(self):
        rows = [self._row("zh", row_id=1), self._row("en", row_id=2)]
        out = filter_library_rows_by_locale(rows, "ja")
        self.assertEqual(out, [])

    def test_empty_when_user_locale_empty(self):
        rows = [self._row("zh", row_id=1), self._row("en", row_id=2)]
        out = filter_library_rows_by_locale(rows, "")
        self.assertEqual(out, [])

    def test_empty_when_no_locale_and_no_en(self):
        rows = [self._row("zh", row_id=1), self._row("ko", row_id=3)]
        out = filter_library_rows_by_locale(rows, "ja")
        self.assertEqual(out, [])


    def test_zero_when_no_eligible(self):
        self.assertEqual(library_companion_pick_count(0, 3), 0)

    def test_zero_when_no_remaining(self):
        self.assertEqual(library_companion_pick_count(10, 0), 0)

    def test_within_bounds(self):
        for _ in range(50):
            n = library_companion_pick_count(eligible_count=100, remaining=2)
            self.assertGreaterEqual(n, 1)
            self.assertLessEqual(n, 2)

    def test_max_three(self):
        for _ in range(50):
            n = library_companion_pick_count(eligible_count=100, remaining=10)
            self.assertGreaterEqual(n, 1)
            self.assertLessEqual(n, 3)


class TestLibraryWinnersPriority(unittest.TestCase):
    def _lib(self, *, row_id, type_key, cids, sort_order, language="en"):
        return SimpleNamespace(
            id=row_id,
            type_key=type_key,
            language=language,
            sort_order=sort_order,
            companion_ids=cids,
            content="x",
        )

    def test_keeps_precise_and_global_as_two_winners(self):
        precise = self._lib(row_id=1, type_key="home_page_show", cids=["abcd1234"], sort_order=0)
        glob = self._lib(row_id=2, type_key="home_page_show", cids=[], sort_order=99)
        winners = _library_winners(
            [glob, precise],
            canonical_event="home_page_show",
            trigger_key="home_page_show",
        )
        self.assertEqual([w.id for w in winners], [1, 2])

    def test_precise_sorted_before_global(self):
        precise = self._lib(row_id=3, type_key="home_page_show", cids=["abcd1234"], sort_order=0)
        glob = self._lib(row_id=4, type_key="home_page_show", cids=[], sort_order=50)
        winners = _library_winners(
            [glob, precise],
            canonical_event="home_page_show",
            trigger_key="home_page_show",
        )
        self.assertEqual(winners[0].id, 3)

    def test_keeps_precise_rows_for_different_companions(self):
        a = self._lib(
            row_id=10,
            type_key="send_message_click_moment",
            cids=["aaaa1111"],
            sort_order=10,
        )
        b = self._lib(
            row_id=11,
            type_key="send_message_click_moment",
            cids=["bbbb2222"],
            sort_order=0,
        )
        winners = _library_winners(
            [a, b],
            canonical_event="send_message_click_moment",
            trigger_key="send_message_click_moment",
        )
        self.assertEqual({w.id for w in winners}, {10, 11})


class TestLibraryToDictHidesGlobalImage(unittest.TestCase):
    def test_global_omits_image_url(self):
        row = SimpleNamespace(
            id=1,
            content="hi",
            language="en",
            type_key="home_page_show",
            companion_ids=[],
            is_active=True,
            sort_order=0,
            created_by="",
            created_at=None,
            updated_at=None,
            image_url="https://cdn.example.com/a.png",
        )
        d = library_to_dict(row, name_map={})
        self.assertEqual(d["scope"], "global")
        self.assertNotIn("image_url", d)

    def test_precise_includes_image_url(self):
        row = SimpleNamespace(
            id=2,
            content="hi",
            language="en",
            type_key="home_page_show",
            companion_ids=["abcd1234"],
            is_active=True,
            sort_order=0,
            created_by="",
            created_at=None,
            updated_at=None,
            image_url="https://cdn.example.com/a.png",
        )
        d = library_to_dict(row, name_map={})
        self.assertEqual(d["scope"], "precise")
        self.assertEqual(d["image_url"], "https://cdn.example.com/a.png")


class TestDispatchLanguage(unittest.TestCase):
    def test_user_language_wins_over_event(self):
        user = MagicMock()
        user.language = "ar"
        self.assertEqual(_resolve_dispatch_language(user, "zh"), "ar")

    def test_new_user_defaults_en_when_event_missing(self):
        user = MagicMock()
        user.language = "en"
        self.assertEqual(_resolve_dispatch_language(user, None), "en")

    def test_empty_user_falls_back_event_then_en(self):
        user = MagicMock()
        user.language = ""
        self.assertEqual(_resolve_dispatch_language(user, "ja-JP"), "ja")
        self.assertEqual(_resolve_dispatch_language(user, None), "en")


class TestCompanionLanguageMatch(unittest.TestCase):
    def test_match_same_short_code(self):
        comp = MagicMock()
        comp.language = "en"
        self.assertTrue(_companion_matches_user_lang(comp, "en"))
        self.assertTrue(_companion_matches_user_lang(comp, "en-US"))

    def test_mismatch_en_user_ar_companion(self):
        comp = MagicMock()
        comp.language = "ar"
        self.assertFalse(_companion_matches_user_lang(comp, "en"))

    def test_empty_sides_do_not_match(self):
        comp = MagicMock()
        comp.language = ""
        self.assertFalse(_companion_matches_user_lang(comp, "en"))
        comp.language = "ar"
        self.assertFalse(_companion_matches_user_lang(comp, ""))
        self.assertFalse(_companion_matches_user_lang(None, "en"))


if __name__ == "__main__":
    unittest.main()
