"""事件统计：按 product_event 聚合，部分事件按人/设备按日去重。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.admin_data_stats import (
    EVENT_ZH,
    UNIQUE_PER_ACTOR_DAY,
    aggregate_product_event_rows,
    canonical_stats_product_event,
    event_uv,
    free_quota_exhausted_days,
    resolve_event_filter,
)


class TestCanonicalProductEvent(unittest.TestCase):
    def test_prefers_product_event(self):
        self.assertEqual(
            canonical_stats_product_event("login", "install"),
            "login",
        )

    def test_strips_test_prefix(self):
        self.assertEqual(
            canonical_stats_product_event("test_login_page_show", "product"),
            "login_page_show",
        )

    def test_fallback_event_type(self):
        self.assertEqual(canonical_stats_product_event("", "install"), "install_attribution")
        self.assertEqual(canonical_stats_product_event("", "login"), "login")
        self.assertEqual(canonical_stats_product_event("", "register"), "register_success")
        self.assertEqual(canonical_stats_product_event("", "purchase"), "purchase_success")
        self.assertEqual(canonical_stats_product_event("", "product"), "")


class TestEventFilter(unittest.TestCase):
    def test_login_is_exact_key(self):
        kw, keys = resolve_event_filter("login")
        self.assertIsNone(kw)
        self.assertEqual(keys, ["login"])
        self.assertEqual(EVENT_ZH.get("login"), "登录成功")


class TestEventUv(unittest.TestCase):
    def test_mixed_uid_and_anon_device(self):
        self.assertEqual(event_uv({1, 2}, {"d1", "d2", "d3"}), 5)

    def test_only_uids(self):
        self.assertEqual(event_uv({1, 2}, set()), 2)

    def test_only_devices(self):
        self.assertEqual(event_uv(set(), {"a", "b"}), 2)


class TestAggregateProductEvents(unittest.TestCase):
    def test_groups_by_product_event_not_firebase(self):
        rows = aggregate_product_event_rows(
            [
                ("2026-09-21", "app_launch", "product", 1, "d1"),
                ("2026-09-21", "app_launch", "product", 1, "d1"),
                ("2026-09-21", "app_launch", "product", 2, "d2"),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_name_en"], "app_launch")
        self.assertEqual(rows[0]["pv"], 3)
        self.assertEqual(rows[0]["uv"], 2)

    def test_ignores_empty_and_maps_legacy_login(self):
        rows = aggregate_product_event_rows(
            [
                ("2026-09-21", "", "login", 10, "d10"),
                ("2026-09-21", "login", "product", 11, "d11"),
            ]
        )
        by_en = {r["event_name_en"]: r for r in rows}
        self.assertEqual(by_en["login"]["pv"], 2)
        self.assertEqual(by_en["login"]["uv"], 2)

    def test_unique_events_dedupe_same_user_same_day(self):
        self.assertIn("register_success", UNIQUE_PER_ACTOR_DAY)
        rows = aggregate_product_event_rows(
            [
                ("2026-09-21", "register_success", "product", 1, "d1"),
                ("2026-09-21", "register_success", "product", 1, "d1"),
                ("2026-09-21", "", "register", 1, "d1"),
                ("2026-09-21", "app_launch", "product", 1, "d1"),
                ("2026-09-21", "app_launch", "product", 1, "d1"),
            ]
        )
        by_en = {r["event_name_en"]: r for r in rows}
        self.assertEqual(by_en["register_success"]["pv"], 1)
        self.assertEqual(by_en["register_success"]["uv"], 1)
        self.assertEqual(by_en["app_launch"]["pv"], 2)
        self.assertEqual(by_en["app_launch"]["uv"], 1)

    def test_no_data_of_im_unique_per_day_not_across_days(self):
        rows = aggregate_product_event_rows(
            [
                ("2026-09-20", "no_data_of_im", "product", 1, "d1"),
                ("2026-09-20", "no_data_of_im", "product", 1, "d1"),
                ("2026-09-21", "no_data_of_im", "product", 1, "d1"),
            ]
        )
        by_day = {(r["date"], r["event_name_en"]): r for r in rows}
        self.assertEqual(by_day[("2026-09-20", "no_data_of_im")]["pv"], 1)
        self.assertEqual(by_day[("2026-09-21", "no_data_of_im")]["pv"], 1)

    def test_filter_exact_key(self):
        rows = aggregate_product_event_rows(
            [
                ("2026-09-21", "app_launch", "product", 1, "d1"),
                ("2026-09-21", "login", "product", 1, "d1"),
            ],
            exact_keys=["login"],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_name_en"], "login")


class TestFreeQuotaExhausted(unittest.TestCase):
    def test_crosses_on_second_day(self):
        """1 号 1 条、2 号 2 条，免费 3 次，用完记在 2 号。"""
        days = free_quota_exhausted_days(
            [
                ("2026-09-01", "u:1", 1),
                ("2026-09-02", "u:1", 2),
            ],
            free_limit=3,
        )
        self.assertEqual(days, {"2026-09-02": {"u:1"}})

    def test_overshoot_counts_once(self):
        days = free_quota_exhausted_days(
            [("2026-09-02", "u:2", 5)],
            free_limit=3,
        )
        self.assertEqual(days, {"2026-09-02": {"u:2"}})

    def test_already_exhausted_before_is_not_counted_again(self):
        days = free_quota_exhausted_days(
            [
                ("2026-09-01", "u:1", 3),
                ("2026-09-02", "u:1", 2),
            ],
            free_limit=3,
        )
        self.assertEqual(days, {"2026-09-01": {"u:1"}})

    def test_same_user_many_sends_one_person(self):
        days = free_quota_exhausted_days(
            [
                ("2026-09-02", "u:1", 1),
                ("2026-09-02", "u:1", 2),
            ],
            free_limit=3,
        )
        self.assertEqual(days["2026-09-02"], {"u:1"})


if __name__ == "__main__":
    unittest.main()
