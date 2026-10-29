"""接口访问日志文件：写入、按大小切割、文件名校验。"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import api_file_log


class TestApiFileLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = mock.patch.dict(
            os.environ,
            {
                "API_LOG_DIR": str(self.dir),
                "API_LOG_ENABLED": "1",
                "API_LOG_MAX_MB": "1",
                "API_LOG_BACKUP_COUNT": "2",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def test_sanitize_hides_token(self):
        q = api_file_log.sanitize_query("token=abc&foo=1")
        self.assertIn("foo=1", q)
        self.assertIn("token=%2A%2A%2A", q)
        self.assertNotIn("abc", q)

    def test_skip_health(self):
        self.assertTrue(api_file_log.should_skip("/api/health"))
        self.assertFalse(api_file_log.should_skip("/api/admin/im/reply-mode"))

    def test_write_creates_current_file(self):
        api_file_log.write_access("GET", "/api/admin/im/reply-mode", 200, 12.3, ip="1.2.3.4")
        path = self.dir / "api.log"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("GET /api/admin/im/reply-mode", text)
        self.assertIn("200", text)
        self.assertIn("ip=1.2.3.4", text)

    def test_health_not_written(self):
        api_file_log.write_access("GET", "/api/health", 200, 1.0)
        self.assertFalse((self.dir / "api.log").exists())

    def test_rotate_and_prune(self):
        with mock.patch.object(api_file_log, "max_bytes", return_value=80):
            for i in range(8):
                api_file_log.write_line(
                    api_file_log.format_line(
                        "GET",
                        f"/api/ping/{i}",
                        200,
                        1.0,
                        when=datetime(2026, 9, 16, 8, 0, i, tzinfo=timezone.utc),
                    )
                )
        names = {p.name for p in self.dir.iterdir()}
        self.assertIn("api.log", names)
        rotated = [n for n in names if n.startswith("api.") and n != "api.log"]
        self.assertTrue(rotated)
        self.assertLessEqual(len(rotated), 2)

    def test_resolve_rejects_traversal(self):
        with self.assertRaises(ValueError):
            api_file_log.resolve_name("../secret.txt")
        with self.assertRaises(ValueError):
            api_file_log.resolve_name("api.log.bak")
        path = api_file_log.resolve_name("api.log")
        self.assertEqual(path, (self.dir / "api.log").resolve())

    def test_list_files_current_first(self):
        api_file_log.write_line("hello\n")
        (self.dir / "api.20260916-080000.log").write_text("old\n", encoding="utf-8")
        files = api_file_log.list_files()
        self.assertEqual(files[0]["name"], "api.log")
        self.assertTrue(files[0]["current"])
        self.assertEqual(files[1]["name"], "api.20260916-080000.log")

    def test_client_ip_prefers_forwarded(self):
        ip = api_file_log.client_ip(
            {"x-forwarded-for": "8.8.8.8, 10.0.0.1", "x-real-ip": "1.1.1.1"},
            "127.0.0.1",
        )
        self.assertEqual(ip, "8.8.8.8")

    def test_runtime_switch_stops_write(self):
        api_file_log.set_enabled(False)
        self.assertFalse(api_file_log.enabled())
        api_file_log.write_access("GET", "/api/ping", 200, 1.0)
        self.assertFalse((self.dir / "api.log").exists())
        api_file_log.set_enabled(True)
        api_file_log.write_access("GET", "/api/ping", 200, 1.0)
        self.assertTrue((self.dir / "api.log").exists())

    def test_env_forced_off(self):
        with mock.patch.dict(os.environ, {"API_LOG_ENABLED": "0"}):
            self.assertTrue(api_file_log.env_forced_off())
            self.assertFalse(api_file_log.enabled())
            self.assertFalse(api_file_log.set_enabled(True))
            self.assertFalse(api_file_log.enabled())

    def test_delete_file_and_all(self):
        api_file_log.write_line("a\n")
        (self.dir / "api.20260916-080000.log").write_text("old\n", encoding="utf-8")
        self.assertTrue(api_file_log.delete_file("api.20260916-080000.log"))
        self.assertFalse((self.dir / "api.20260916-080000.log").exists())
        n = api_file_log.delete_all()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(api_file_log.list_files(), [])

    def test_skip_admin_log_routes(self):
        self.assertTrue(api_file_log.should_skip("/api/admin/logs"))
        self.assertTrue(api_file_log.should_skip("/api/admin/logs/file"))


if __name__ == "__main__":
    unittest.main()
