"""福利模板删除：必须真正删模板行；只解绑商品，不删商品。"""
from __future__ import annotations

import os
import unittest


class TestAdminDeleteTemplateIsHard(unittest.TestCase):
    def test_delete_removes_template_not_products(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "benefits.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        start = src.index("async def admin_delete_template")
        block = src[start : start + 1200]
        self.assertIn("db.delete(template)", block)
        self.assertNotIn("template.is_active = False", block)
        self.assertIn("BenefitTemplateProductORM", block)
        self.assertIn("BenefitTemplateRegionORM", block)
        self.assertIn(".delete(synchronize_session=False)", block)
        # 不得删商品表
        self.assertNotIn("db.delete(product)", block)
        self.assertNotIn("BenefitProductORM", block)


if __name__ == "__main__":
    unittest.main()
