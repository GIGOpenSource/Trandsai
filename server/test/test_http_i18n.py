"""HTTP 错误文案随 Accept-Language 切换。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.i18n import (
    auth_expired_message,
    auth_required_message,
    companion_forbidden_message,
    companion_not_found_message,
)


class TestHttpI18n(unittest.TestCase):
    def test_auth_required_zh_en(self):
        self.assertEqual(auth_required_message("zh-CN"), "请先登录")
        self.assertEqual(auth_required_message("en-US"), "Please sign in")
        self.assertEqual(auth_required_message(None), "Please sign in")

    def test_auth_expired_ja(self):
        self.assertIn("ログイン", auth_expired_message("ja"))

    def test_companion_messages(self):
        self.assertIn("exist", companion_not_found_message("en").lower())
        self.assertTrue(companion_forbidden_message("en"))
        self.assertIn("智能体", companion_not_found_message("zh"))

    def test_vip_and_limit_en(self):
        from core.i18n import (
            agent_limit_message,
            credits_insufficient_message,
            vip_required_message,
        )

        self.assertIn("VIP", vip_required_message("en"))
        self.assertIn("credit", credits_insufficient_message("en").lower())
        self.assertIn("3", agent_limit_message(3, "en"))
        self.assertIn("3", agent_limit_message(3, "zh"))

    def test_post_and_ticket(self):
        from core.i18n import (
            forbidden_message,
            post_not_found_message,
            ticket_issue_failed_message,
        )

        self.assertIn("Post", post_not_found_message("en"))
        self.assertIn("ticket", ticket_issue_failed_message("en").lower())
        self.assertIn("permission", forbidden_message("en").lower())

    def test_validation_en(self):
        from core.i18n import validation_message

        self.assertIn("required", validation_message("name_city_personality_required", "en").lower())
        self.assertIn("Moment", validation_message("moment_not_found", "en"))
        self.assertIn("id_token", validation_message("missing_id_token", "en"))
        self.assertIn("receipt", validation_message("missing_receipt_data", "en").lower())
        self.assertIn("Apple", validation_message("apple_login_failed", "en"))
        self.assertIn("Username", validation_message("username_empty", "en"))

    def test_stored_gender_aliases(self):
        from core.gender import stored_gender

        self.assertEqual(stored_gender("male"), "男")
        self.assertEqual(stored_gender("female"), "女")
        self.assertEqual(stored_gender("secret"), "保密")
        self.assertEqual(stored_gender(""), "女")
        self.assertEqual(stored_gender("male", default=""), "男")

    def test_canonical_user_gender(self):
        from core.gender import canonical_user_gender

        self.assertEqual(canonical_user_gender("女"), "female")
        self.assertEqual(canonical_user_gender("男"), "male")
        self.assertEqual(canonical_user_gender("Male"), "male")
        self.assertEqual(canonical_user_gender("保密"), "confidential")
        self.assertEqual(canonical_user_gender(""), "")


class TestLocaleGettext(unittest.TestCase):
    def test_api_msgid_zh_en_ja(self):
        from core.i18n import _, bind_request_ui_lang, reset_ui_lang

        tok = bind_request_ui_lang(lang="zh")
        try:
            self.assertEqual(_("评论失败"), "评论失败")
            self.assertEqual(_("服务器内部错误"), "服务器内部错误")
        finally:
            reset_ui_lang(tok)

        tok = bind_request_ui_lang(lang="en")
        try:
            self.assertEqual(_("评论失败"), "Failed to comment")
            self.assertEqual(_("服务器内部错误"), "Internal server error")
        finally:
            reset_ui_lang(tok)

        tok = bind_request_ui_lang(lang="ja")
        try:
            self.assertIn("コメント", _("评论失败"))
        finally:
            reset_ui_lang(tok)

    def test_unknown_msgid_falls_back(self):
        from core.i18n import _, bind_request_ui_lang, reset_ui_lang

        tok = bind_request_ui_lang(lang="en")
        try:
            self.assertEqual(_("这句没有进词表"), "这句没有进词表")
        finally:
            reset_ui_lang(tok)

    def test_display_names_and_busy_pt(self):
        from core.i18n import _, bind_request_ui_lang, reset_ui_lang

        tok = bind_request_ui_lang(lang="en")
        try:
            self.assertEqual(_("我"), "Me")
            self.assertEqual(_("匿名用户"), "Anonymous")
            self.assertEqual(_("已注销"), "Deleted")
            self.assertEqual(_("用户_{id}").format(id="abc123"), "User_abc123")
        finally:
            reset_ui_lang(tok)

        self.assertIn("ocupados", _("现在有点忙，稍后再聊～", lang="pt"))
        self.assertEqual(_("您的问题已收到，我们会尽快处理", lang="en"),
                         "We've received your message and will get back to you soon.")

    def test_payment_names_and_stored_description(self):
        from core.i18n import (
            _,
            bind_request_ui_lang,
            localize_credit_description,
            localize_fallback_user_name,
            reset_ui_lang,
            vip_plan_display_name,
        )

        tok = bind_request_ui_lang(lang="en")
        try:
            self.assertEqual(vip_plan_display_name("weekly"), "Weekly VIP")
            self.assertEqual(
                localize_credit_description("每日VIP赠送 24 积分"),
                "Daily VIP gift of 24 credits",
            )
            self.assertEqual(
                localize_credit_description("开通VIP monthly，赠送50积分"),
                "Activated Monthly VIP, gifted 50 credits",
            )
            self.assertEqual(
                localize_credit_description("创建自定义智能体"),
                "Created a custom companion",
            )
            self.assertEqual(localize_fallback_user_name("匿名用户"), "Anonymous")
            self.assertEqual(localize_fallback_user_name("用户_ab12cd"), "User_ab12cd")
            self.assertEqual(localize_fallback_user_name("Alice"), "Alice")
        finally:
            reset_ui_lang(tok)

    def test_empty_ui_lang_defaults_to_en(self):
        from core.i18n import normalize_ui_language
        from services.prompts.resolve import resolve_lang

        self.assertEqual(normalize_ui_language(None), "en")
        self.assertEqual(normalize_ui_language(""), "en")
        self.assertEqual(resolve_lang(), "en")

    def test_persona_filler_lang_override(self):
        from core.i18n import _

        self.assertEqual(_("未指定", lang="en"), "unspecified")
        self.assertEqual(_("未指定", lang="ja"), "指定なし")
        bg = _("{name}生活在{city}，性格偏向{personality}。", lang="en").format(
            name="Ada", city="Tokyo", personality="calm"
        )
        self.assertIn("Ada lives in Tokyo", bg)


if __name__ == "__main__":
    unittest.main()
