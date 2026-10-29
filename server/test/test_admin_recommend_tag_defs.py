"""标签字典：占用检测、新管理路由。"""
from __future__ import annotations

import os
import re
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.recommend_tags import strategies_using_tag, tag_def_to_dict


class TestStrategiesUsingTag(unittest.TestCase):
    def test_user_side_exact(self):
        rows = [
            SimpleNamespace(id=1, name="A", user_tags=["gender:male"], agent_tags=["cute"]),
            SimpleNamespace(id=2, name="B", user_tags=["gender:female"], agent_tags=[]),
        ]
        used = strategies_using_tag(rows, "user", "gender:male")
        self.assertEqual(used, [{"id": 1, "name": "A"}])

    def test_agent_side(self):
        rows = [
            SimpleNamespace(id=3, name="C", user_tags=[], agent_tags=["gentle", "cute"]),
        ]
        self.assertEqual(strategies_using_tag(rows, "agent", "cute"), [{"id": 3, "name": "C"}])
        self.assertEqual(strategies_using_tag(rows, "agent", "ops"), [])

    def test_empty_tags(self):
        rows = [SimpleNamespace(id=4, name="D", user_tags=None, agent_tags=None)]
        self.assertEqual(strategies_using_tag(rows, "user", "x"), [])


class TestTagDefToDict(unittest.TestCase):
    def test_fields(self):
        row = SimpleNamespace(
            key="vip",
            side="agent",
            group="ops",
            label_zh="VIP",
            label_en="",
            enabled=1,
            source=None,
        )
        self.assertEqual(
            tag_def_to_dict(row),
            {
                "key": "vip",
                "side": "agent",
                "group": "ops",
                "label_zh": "VIP",
                "label_en": "",
                "enabled": True,
                "source": "ops",
            },
        )


class TestTagDefRoutesExist(unittest.TestCase):
    def test_old_list_only(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "admin_recommend.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r'@router\.get\("/api/admin/recommend/tag-defs"')
        self.assertNotRegex(src, r'@router\.post\("/api/admin/recommend/tag-defs"')
        self.assertNotRegex(src, r'@router\.delete\("/api/admin/recommend/tag-defs/')
        self.assertNotRegex(src, r'@router\.patch\("/api/admin/recommend/tag-defs/')
        self.assertNotRegex(src, r'@router\.put\("/api/admin/recommend/tag-defs/')

    def test_dictionary_crud(self):
        path = os.path.join(os.path.dirname(__file__), "..", "api", "admin_recommend.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        prefix = r"/api/admin/recommend/tag-dictionary"
        self.assertRegex(src, rf'@router\.get\("{prefix}"')
        self.assertRegex(src, rf'@router\.get\("{prefix}/meta"')
        self.assertRegex(src, rf'@router\.get\("{prefix}/{{side}}/{{key}}"')
        self.assertRegex(src, rf'@router\.post\("{prefix}"')
        self.assertRegex(src, rf'@router\.put\("{prefix}/{{side}}/{{key}}"')
        self.assertRegex(src, rf'@router\.patch\("{prefix}/{{side}}/{{key}}"')
        self.assertRegex(src, rf'@router\.patch\("{prefix}/{{side}}/{{key}}/active"')
        self.assertRegex(src, rf'@router\.delete\("{prefix}/{{side}}/{{key}}"')
        self.assertIn("paginated(", src)
        self.assertIn("return ok(data=", src)
        self.assertIn("return fail(", src)
        fn = re.search(r"async def admin_delete_tag_dictionary\([\s\S]{0,400}\)", src)
        self.assertIsNotNone(fn)
        self.assertIn("admin_auth_required", fn.group(0))


if __name__ == "__main__":
    unittest.main()
