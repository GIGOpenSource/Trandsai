"""性格标签解析：generate 不再回传分组字段，只保留 key/label 工具。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.personality_catalog import (
    join_personality_labels,
    keys_from_personality_text,
    normalize_personality_keys,
    personality_label_map,
)


class TestPersonalityResolve(unittest.TestCase):
    def test_mixed_keys_and_labels_en(self):
        keys = keys_from_personality_text("humorous、procrastinator、warmly steady")
        self.assertEqual(keys, ["humorous", "procrastinator", "warm_steady"])
        m = personality_label_map("en")
        self.assertEqual(
            join_personality_labels([m[k] for k in keys], "en"),
            "humorous, procrastinator, warmly steady",
        )

    def test_normalize_keys_from_labels(self):
        keys = normalize_personality_keys(["warmly steady", "humorous"])
        self.assertEqual(keys, ["warm_steady", "humorous"])

    def test_zh_join_sep(self):
        keys = keys_from_personality_text("温柔、理性")
        self.assertEqual(keys, ["gentle", "rational"])
        m = personality_label_map("zh")
        self.assertEqual(join_personality_labels([m[k] for k in keys], "zh"), "温柔、理性")

    def test_tr_ar_labels_not_english_fallback(self):
        m = personality_label_map("tr")
        self.assertEqual(m["sporty"], "sportif")
        self.assertEqual(m["rebellious"], "asi")
        self.assertEqual(join_personality_labels([m["sporty"], m["rebellious"]], "tr"), "sportif, asi")
        ar = personality_label_map("ar")
        self.assertEqual(ar["sporty"], "رياضي")
        for lang in ("ar", "fa", "ru", "bg", "tr"):
            labels = personality_label_map(lang)
            self.assertNotEqual(labels["calm"], "calm")
            self.assertNotEqual(labels["sporty"], "sporty")


if __name__ == "__main__":
    unittest.main()
