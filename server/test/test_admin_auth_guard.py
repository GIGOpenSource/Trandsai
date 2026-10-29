"""Admin mutating routes (except login) must Depends(admin_auth_required)."""
from __future__ import annotations

import os
import re
import unittest

API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
SKIP_FUNCS = {"admin_login"}
ROUTE_RE = re.compile(
    r"@router\.(post|put|patch|delete|get)\(\s*\"([^\"]+)\"",
    re.I,
)
FUNC_RE = re.compile(r"async def (\w+)\(")


class TestAdminAuthGuard(unittest.TestCase):
    def test_admin_write_routes_require_auth(self):
        missing = []
        for name in os.listdir(API_DIR):
            if not name.startswith("admin") or not name.endswith(".py"):
                continue
            path = os.path.join(API_DIR, name)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            # Walk decorator + next async def
            for m in ROUTE_RE.finditer(src):
                method, route = m.group(1).lower(), m.group(2)
                rest = src[m.end() : m.end() + 800]
                fm = FUNC_RE.search(rest)
                if not fm:
                    continue
                fn = fm.group(1)
                if fn in SKIP_FUNCS:
                    continue
                if route.startswith("/api/admin") and method in (
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "get",
                ):
                    block = rest[: fm.end() + 400]
                    if "admin_auth_required" not in block and "admin_auth_required" not in src[m.start() : fm.end()]:
                        missing.append(f"{name}:{fn} {method.upper()} {route}")
        self.assertEqual(missing, [], msg="unauthenticated admin routes:\n" + "\n".join(missing))

    def test_admin_prefix_bypasses_user_write_middleware(self):
        path = os.path.join(os.path.dirname(__file__), "..", "core", "middleware.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"/api/admin"', src)
        self.assertNotIn(
            '"/api/admin/login"',
            src,
            "login-only whitelist still 401s admin POSTs (Bearer vs x-token)",
        )

    def test_regenerate_image_has_admin_auth(self):
        path = os.path.join(API_DIR, "admin_moments.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("admin_regenerate_moment_image", src)
        idx = src.index("async def admin_regenerate_moment_image")
        head = src[max(0, idx - 400) : idx]
        self.assertIn("admin_auth_required", head)


if __name__ == "__main__":
    unittest.main()
