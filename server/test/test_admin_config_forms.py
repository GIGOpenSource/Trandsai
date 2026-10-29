"""校验配置组表单 schema、normalize、允许的 config_type。"""
from __future__ import annotations

import unittest

from api import admin_config as ac
from services.affection_floor import normalize_floor_config_json
from services.say_hi_quota import normalize_say_hi_daily_config_json


class TestConfigGroupForms(unittest.TestCase):
    def test_allowed_types_include_payment(self):
        self.assertIn("payment", ac._ALLOWED_CONFIG_TYPES)
        self.assertIn("system", ac._ALLOWED_CONFIG_TYPES)

    def test_form_schemas_cover_product_keys(self):
        for key in (
            "free_turns_limit",
            "say_hi_daily_free_limit",
            "default_affection_floor",
            "vip_plans",
            "agent",
            "model_service",
            "image_generation",
            "image_gemini",
            "image_xai",
        ):
            self.assertIn(key, ac._FORM_SCHEMAS, msg=key)
            schema = ac._FORM_SCHEMAS[key]
            self.assertTrue(schema.get("fields") or schema.get("layout"))
            self.assertTrue(schema.get("config_type"))

    def test_normalize_free_turns(self):
        out = ac._normalize_config_json_for_key(
            "free_turns_limit", {"free_turns_limit": "7"}
        )
        self.assertEqual(out["free_turns_limit"], 7)

    def test_normalize_say_hi(self):
        out = ac._normalize_config_json_for_key(
            "say_hi_daily_free_limit", {"say_hi_daily_free_limit": 100}
        )
        self.assertEqual(out["say_hi_daily_free_limit"], 100)
        out2 = normalize_say_hi_daily_config_json({"say_hi_daily_free_limit": -3})
        self.assertEqual(out2["say_hi_daily_free_limit"], 0)

    def test_normalize_affection(self):
        out = normalize_floor_config_json({"default_affection_floor": 5})
        self.assertEqual(out["default_affection_floor"], 20)
        out2 = ac._normalize_config_json_for_key(
            "default_affection_floor", {"default_affection_floor": 150}
        )
        self.assertEqual(out2["default_affection_floor"], 100)

    def test_say_hi_schema_field_path(self):
        fields = ac._FORM_SCHEMAS["say_hi_daily_free_limit"]["fields"]
        self.assertEqual(fields[0]["path"], "say_hi_daily_free_limit")
        self.assertEqual(fields[0]["default"], 10)

    def test_vip_ignores_legacy_note(self):
        vip = ac._FORM_SCHEMAS["vip_plans"]
        self.assertIn("chat_cost", vip.get("ignore_legacy_paths") or [])


if __name__ == "__main__":
    unittest.main()
