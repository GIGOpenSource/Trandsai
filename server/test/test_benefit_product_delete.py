"""福利商品删除：必须真正删行，不能只下架。"""
from __future__ import annotations

import os
import unittest


class TestAdminDeleteProductIsHard(unittest.TestCase):
    def test_delete_removes_row(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "benefits.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("async def admin_delete_product")
        block = src[start : start + 1800]
        self.assertIn("db.delete(product)", block)
        self.assertNotIn("product.is_active = False", block)
        self.assertIn("商品仍绑定模板，不能删除", block)
        self.assertIn("BenefitProductTitleORM", block)


if __name__ == "__main__":
    unittest.main()
