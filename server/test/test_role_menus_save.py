"""角色菜单保存：diff + 校正 id 序列，避免 pkey 冲突。"""
from __future__ import annotations

import os
import unittest


class TestApplyRoleMenus(unittest.TestCase):
    def test_apply_uses_diff_and_seq_sync(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "admin.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("def _apply_role_menus")
        end = src.index("\n@router.get(\"/api/admin/admin-users\"", start)
        block = src[start:end]
        self.assertIn("_sync_admin_role_menus_id_seq", block)
        self.assertIn("to_add", block)
        self.assertIn("to_del", block)
        self.assertNotIn(
            "AdminRoleMenuORM.role_key == role_key).delete(synchronize_session=False)\n    for mk in sorted(valid)",
            block,
        )


if __name__ == "__main__":
    unittest.main()
