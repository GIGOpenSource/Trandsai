"""Comment → IM outreach guards (no live DB / LLM)."""
from __future__ import annotations

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _fn_source(rel: str, name: str) -> str:
    src = _read(rel)
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = src.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"function {name} not found in {rel}")


class TestCommentOutreach(unittest.TestCase):
    def test_schedule_does_not_drop_already_chatted(self):
        src = _fn_source("services/outreach.py", "schedule_comment_outreach")
        self.assertNotIn("_has_chatted", src)
        self.assertNotIn("adjust_to_send_window", src)
        self.assertIn("ui_lang", src)
        self.assertIn("randint(8, 25)", src)
        self.assertIn("COMMENT_DM_PROBABILITY", src)
        self.assertIn("reason=probability", src)

    def test_process_comment_dm_not_skipped_for_prior_chat(self):
        src = _fn_source("services/outreach.py", "_process_one")
        compact = src.replace(" ", "").replace("\n", "")
        self.assertIn("JOB_COMMENT_DM", src)
        self.assertIn("JOB_LIKE_DM,JOB_CREATE_WELCOME", compact)
        self.assertIn("JOB_CREATE_WELCOME,JOB_COMMENT_DM", compact)

    def test_comment_prompt_uses_persona_moment_and_comment(self):
        src = _fn_source("services/outreach.py", "_generate_dm_text")
        self.assertIn("companion_blurb", src)
        self.assertIn("Their comment", src)
        self.assertIn("Moment you posted", src)
        self.assertIn("Stay in character", src)

    def test_comment_api_passes_ui_lang(self):
        src = _fn_source("api/moments.py", "api_add_comment")
        self.assertIn("ui_lang", src)
        self.assertIn("resolve_lang", src)

    def test_comment_reply_notification_payload_in_source(self):
        src = _read("services/moments.py")
        self.assertIn("{name}回复了你的评论", src)
        self.assertIn("{name} replied to your comment", src)
        self.assertIn("def build_comment_reply_notification", src)
        notify_src = _fn_source("services/moments.py", "_notify_user_of_comment_reply")
        self.assertIn("SystemNotificationORM", notify_src)
        self.assertIn("moment_id", notify_src)
        add_src = _fn_source("services/moments.py", "add_user_comment")
        self.assertIn("_notify_user_of_comment_reply", add_src)
        self.assertIn("reply_text", add_src)
        for name in ("services/outreach.py", "services/moments.py", "api/moments.py", "api/admin_ops.py"):
            ast.parse(_read(name))

    def test_notifications_api_returns_unified_fields(self):
        src = _fn_source("api/admin_ops.py", "_public_notification_item")
        for key in ("id", "title", "content", "language", "user_id", "companion_id", "moment_id", "avatar_url", "created_at"):
            self.assertIn(key, src)


if __name__ == "__main__":
    unittest.main()
