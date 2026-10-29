"""客户端版本号聊天提示词：解析、开关、注册时间窗。"""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.version_prompt import (
    apply_version_prompt_prefix,
    extract_app_version,
    format_version_prefix,
    normalize_app_version,
    resolve_version_prompt,
)


def _row(prompt, enabled=True, start=None, end=None, pid=1):
    return {
        "id": pid,
        "is_enabled": enabled,
        "prompt": prompt,
        "apply_registered_start_at": start,
        "apply_registered_end_at": end,
    }


class TestVersionPrompt(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_app_version("v1.1.1"), "1.1.1")
        self.assertEqual(normalize_app_version(" 1.2.0, 1.2.0 "), "1.2.0")
        self.assertEqual(normalize_app_version(""), "")

    def test_extract_header_then_query(self):
        self.assertEqual(
            extract_app_version({"X-App-Version": "2.0.0"}, {"app_version": "9.9.9"}),
            "2.0.0",
        )
        self.assertEqual(
            extract_app_version({}, {"app_version": "1.1.1"}),
            "1.1.1",
        )

    @patch("services.version_prompt._load_map")
    def test_missing_version_uses_111_if_enabled(self, load_map):
        load_map.return_value = {"1.1.1": _row("KEEP_GREEN")}
        self.assertIn("KEEP_GREEN", resolve_version_prompt(""))
        self.assertIn("KEEP_GREEN", resolve_version_prompt("9.9.9"))
        self.assertIn("Do not change the reply language", resolve_version_prompt(""))

    @patch("services.version_prompt._load_map")
    def test_disabled_not_imported(self, load_map):
        load_map.return_value = {"1.1.1": _row("KEEP_GREEN", enabled=False)}
        self.assertEqual(resolve_version_prompt(""), "")
        self.assertEqual(apply_version_prompt_prefix("persona", ""), "persona")

    @patch("services.version_prompt._load_map")
    def test_enabled_version_highest_prefix(self, load_map):
        load_map.return_value = {
            "1.1.1": _row("A"),
            "2.0.0": _row("B"),
        }
        out = resolve_version_prompt("2.0.0")
        self.assertIn("B", out)
        self.assertTrue(out.startswith("[HIGHEST PRIORITY"))
        self.assertTrue(apply_version_prompt_prefix("persona", "2.0.0").startswith("[HIGHEST PRIORITY"))

    @patch("services.version_prompt._load_map")
    def test_registration_window(self, load_map):
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        end = datetime(2026, 10, 31, tzinfo=timezone.utc)
        load_map.return_value = {"1.1.1": _row("WIN", start=start, end=end)}
        inside = datetime(2026, 10, 15, tzinfo=timezone.utc)
        outside = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.assertIn("WIN", resolve_version_prompt("1.1.1", registered_at=inside))
        self.assertEqual(resolve_version_prompt("1.1.1", registered_at=outside), "")
        self.assertEqual(resolve_version_prompt("1.1.1", registered_at=None), "")

    @patch("services.version_prompt._load_exclusions")
    @patch("services.version_prompt._load_map")
    def test_removed_user_skips_prompt(self, load_map, load_excl):
        load_map.return_value = {"2.0.0": _row("STORE", pid=8)}
        load_excl.return_value = {8: {42}}
        self.assertEqual(resolve_version_prompt("2.0.0", user_id=42), "")
        self.assertIn("STORE", resolve_version_prompt("2.0.0", user_id=7))

    def test_format_does_not_double_wrap(self):
        once = format_version_prefix("HELLO")
        twice = format_version_prefix(once)
        self.assertEqual(once.count("[HIGHEST PRIORITY"), 1)
        self.assertEqual(twice, once)


if __name__ == "__main__":
    unittest.main()
