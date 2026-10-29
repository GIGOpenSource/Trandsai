from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.openapi_tags import apply_openapi_tags, tag_for_path  # noqa: E402


class TestOpenapiTags(unittest.TestCase):
    def test_admin_split(self):
        self.assertEqual(tag_for_path("/api/admin/login"), "管理后台 · 登录权限")
        self.assertEqual(tag_for_path("/api/admin/users"), "管理后台 · 用户")
        self.assertEqual(tag_for_path("/api/admin/companions"), "管理后台 · 智能体")
        self.assertEqual(tag_for_path("/api/admin/content/companions"), "管理后台 · 内容管理")
        self.assertEqual(tag_for_path("/api/admin/strategy-center/independent"), "管理后台 · 消息策略")
        self.assertEqual(tag_for_path("/api/admin/im/sessions"), "管理后台 · IM 会话")
        self.assertEqual(tag_for_path("/api/admin/audit/posts"), "管理后台 · 审核")
        self.assertEqual(tag_for_path("/api/admin/pending-orders"), "管理后台 · 预订单")
        self.assertEqual(tag_for_path("/api/admin/stats/dau"), "管理后台 · 数据统计")
        self.assertEqual(tag_for_path("/api/admin/stats"), "管理后台 · 其它")

    def test_user_default_paths(self):
        self.assertEqual(tag_for_path("/companions", ["default"]), "伴侣")
        self.assertEqual(tag_for_path("/ws/chat/{companion_id}"), "聊天")
        self.assertEqual(tag_for_path("/api/im/translate"), "聊天")
        self.assertEqual(tag_for_path("/api/knowledge/search"), "知识库")
        self.assertEqual(tag_for_path("/api/health"), "系统")

    def test_keep_existing_user_tags(self):
        self.assertEqual(tag_for_path("/api/payment/orders", ["支付"]), "支付")
        self.assertEqual(tag_for_path("/api/posts", ["社区"]), "社区")

    def test_apply_rewrites_schema(self):
        schema = {
            "paths": {
                "/api/admin/login": {"post": {"tags": ["管理后台"]}},
                "/companions": {"get": {"tags": []}},
                "/api/payment/x": {"post": {"tags": ["支付"]}},
            }
        }
        apply_openapi_tags(schema)
        self.assertEqual(schema["paths"]["/api/admin/login"]["post"]["tags"], ["管理后台 · 登录权限"])
        self.assertEqual(schema["paths"]["/companions"]["get"]["tags"], ["伴侣"])
        self.assertEqual(schema["paths"]["/api/payment/x"]["post"]["tags"], ["支付"])
        self.assertTrue(any(t["name"] == "管理后台 · 内容管理" for t in schema["tags"]))


if __name__ == "__main__":
    unittest.main()
