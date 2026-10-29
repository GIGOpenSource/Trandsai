"""商品多国家名称：校验、Accept-Language 选取、绑定替换。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.benefit_titles import (
    normalize_titles_payload,
    pick_title,
    replace_product_titles,
    title_lookup_keys,
)
from api.benefits import (  # noqa: E402
    _clamp_limited_duration,
    _parse_limited_fields,
    _product_to_dict,
)


class TestNormalizeTitles(unittest.TestCase):
    def test_country_keys_uppercased(self):
        out = normalize_titles_payload({"us": "Weekly VIP", "JP": "週間VIP"})
        self.assertEqual(out, {"US": "Weekly VIP", "JP": "週間VIP"})

    def test_empty_and_blank_dropped(self):
        out = normalize_titles_payload({"US": "A", "JP": "  ", "DE": None})
        self.assertEqual(out, {"US": "A"})

    def test_all_allowed(self):
        out = normalize_titles_payload({"ALL": "VIP"})
        self.assertEqual(out, {"ALL": "VIP"})

    def test_invalid_key(self):
        with self.assertRaises(ValueError):
            normalize_titles_payload({"USA": "x"})

    def test_not_object(self):
        with self.assertRaises(ValueError):
            normalize_titles_payload(["US", "JP"])


class TestPickTitle(unittest.TestCase):
    NAMES = {"US": "Weekly VIP", "JP": "週間VIP", "CN": "周卡VIP"}

    def test_en_us_picks_us(self):
        self.assertEqual(pick_title(self.NAMES, "en-US,en;q=0.9"), "Weekly VIP")

    def test_ja_maps_to_jp(self):
        self.assertEqual(pick_title(self.NAMES, "ja"), "週間VIP")
        self.assertEqual(pick_title(self.NAMES, "ja-JP"), "週間VIP")

    def test_zh_cn_picks_cn(self):
        self.assertEqual(pick_title(self.NAMES, "zh-CN"), "周卡VIP")

    def test_miss_returns_none(self):
        self.assertIsNone(pick_title(self.NAMES, "de-DE"))
        self.assertIsNone(pick_title({}, "en-US"))
        self.assertIsNone(pick_title(None, "en-US"))

    def test_all_fallback(self):
        self.assertEqual(pick_title({"ALL": "VIP"}, "de-DE"), "VIP")

    def test_lookup_keys_order(self):
        self.assertEqual(title_lookup_keys("en-US")[:2], ["US", "EN"])
        self.assertIn("JP", title_lookup_keys("ja"))


class TestProductToDictPublicShape(unittest.TestCase):
    def _product(self):
        return SimpleNamespace(
            id=12,
            product_type="point",
            platform="apple",
            title="Points 4.99",
            period="",
            price_usd=4.99,
            discount_pct=0,
            credits_amount=250,
            daily_gift_credits=0,
            max_agents=0,
            daily_avg_price=0,
            store_product_id="pack_250",
            apple_product_id="Points4.99",
            sort_order=10,
            is_active=True,
            extra="{}",
            created_at=None,
            updated_at=None,
        )

    def test_public_title_from_accept_language_no_titles_field(self):
        d = _product_to_dict(
            self._product(),
            lang="en",
            localize=True,
            titles_map={"US": "Points 4.99", "JP": "ポイント 4.99"},
            accept_language="en-US",
        )
        self.assertEqual(d["title"], "Points 4.99")
        self.assertNotIn("titles", d)
        self.assertEqual(d["id"], 12)
        self.assertEqual(d["product_type"], "point")
        self.assertEqual(d["platform"], "apple")

    def test_public_ja_title(self):
        d = _product_to_dict(
            self._product(),
            lang="ja",
            localize=True,
            titles_map={"US": "Points 4.99", "JP": "ポイント 4.99"},
            accept_language="ja",
        )
        self.assertEqual(d["title"], "ポイント 4.99")
        self.assertNotIn("titles", d)

    def test_admin_keeps_default_title_and_titles_map(self):
        d = _product_to_dict(
            self._product(),
            localize=False,
            titles_map={"US": "Points 4.99", "JP": "ポイント 4.99"},
            include_titles=True,
        )
        self.assertEqual(d["title"], "Points 4.99")
        self.assertEqual(d["titles"]["JP"], "ポイント 4.99")

    def test_limited_defaults_off(self):
        d = _product_to_dict(self._product(), localize=False)
        self.assertFalse(d["is_limited_offer"])
        self.assertEqual(d["limited_duration_sec"], 0)

    def test_limited_clamps_duration(self):
        p = self._product()
        p.is_limited_offer = True
        p.limited_duration_sec = 99999
        d = _product_to_dict(p, localize=False)
        self.assertTrue(d["is_limited_offer"])
        self.assertEqual(d["limited_duration_sec"], 3600)


class TestLimitedOfferFields(unittest.TestCase):
    def test_clamp_off_is_zero(self):
        self.assertEqual(_clamp_limited_duration(1800, enabled=False), 0)

    def test_clamp_default_and_max(self):
        self.assertEqual(_clamp_limited_duration(0, enabled=True), 1800)
        self.assertEqual(_clamp_limited_duration(900, enabled=True), 900)
        self.assertEqual(_clamp_limited_duration(5000, enabled=True), 3600)

    def test_parse_create_body(self):
        on, sec = _parse_limited_fields({"is_limited_offer": True})
        self.assertTrue(on)
        self.assertEqual(sec, 1800)
        off, z = _parse_limited_fields({})
        self.assertFalse(off)
        self.assertEqual(z, 0)


class _FakeQuery:
    def __init__(self, store):
        self.store = store
        self._deleted = False

    def filter(self, *_a, **_k):
        return self

    def delete(self, synchronize_session=False):
        self.store.clear()
        return 0


class _FakeDb:
    def __init__(self):
        self.added = []
        self.store = []

    def query(self, _model):
        return _FakeQuery(self.store)

    def add(self, obj):
        self.added.append(obj)


class TestReplaceTitles(unittest.TestCase):
    def test_replace_adds_rows(self):
        db = _FakeDb()
        replace_product_titles(db, 12, {"US": "Weekly VIP", "JP": "週間VIP"})
        self.assertEqual(len(db.added), 2)
        regions = {row.region_code for row in db.added}
        self.assertEqual(regions, {"US", "JP"})


if __name__ == "__main__":
    unittest.main()
