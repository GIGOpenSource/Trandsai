"""广告户成人路径：最高档直白指引 + 未成年人一句底线；非广告仍走档位。"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

# 无 sqlalchemy 的本地环境也能跑：先 stub DB 依赖
if "sqlalchemy" not in sys.modules:
    sys.modules["sqlalchemy"] = MagicMock()
    sys.modules["sqlalchemy.orm"] = MagicMock()
if "core.database" not in sys.modules:
    _db = types.ModuleType("core.database")
    for _name in (
        "ConfigGroupORM",
        "UserORM",
        "AgentConfigORM",
        "CompanionAgentConfigORM",
    ):
        setattr(_db, _name, type(_name, (object,), {}))
    _db.get_db = MagicMock()
    sys.modules["core.database"] = _db

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.affection_floor import (
    bind_prompt_ads_user,
    reset_prompt_ads_user,
)
from services.agent_prompts import adult_intimacy_guidance
from services.agent_utils import get_content_restriction
from services.dialogue_phase2 import chat_rules_block
from services.dialogue_quality import enrich_human_prompt, rewrite_instruction


class TestAdsAdultGuidance(unittest.TestCase):
    def tearDown(self):
        # 避免 ContextVar 泄漏到其它用例
        try:
            reset_prompt_ads_user(bind_prompt_ads_user(is_ads=False))
        except Exception:
            pass

    def test_ads_skips_band_and_minors_only_floor(self):
        token = bind_prompt_ads_user(is_ads=True)
        try:
            text = adult_intimacy_guidance("zh", 0)
            self.assertIn("广告户", text)
            self.assertIn("dirty talk", text.lower() + text)
            self.assertIn("勿涉及未成年人", text)
            self.assertNotIn("非自愿", text)
            self.assertNotIn("法律底线（始终）", text)
            # 亲密度 0 也不掉进试探档
            self.assertNotIn("试探", text)
        finally:
            reset_prompt_ads_user(token)

    def test_organic_keeps_full_legal_floor(self):
        token = bind_prompt_ads_user(is_ads=False)
        try:
            text = adult_intimacy_guidance("zh", 80)
            self.assertIn("法律底线", text)
            self.assertIn("未成年人", text)
            self.assertNotIn("广告户", text)
        finally:
            reset_prompt_ads_user(token)

    def test_get_content_restriction_ads_bypasses_legacy(self):
        os.environ["CONTENT_RESTRICTION"] = "legacy"
        # motive_layer 在 import 时读 env；强制刷新模块标志
        import services.motive_layer as ml

        prev = ml.CONTENT_RESTRICTION_MODE
        ml.CONTENT_RESTRICTION_MODE = "legacy"
        token = bind_prompt_ads_user(is_ads=True)
        try:
            text = get_content_restriction("zh", 0)
            self.assertIn("广告户", text)
            self.assertNotIn("绝对禁止回复任何色情", text)
        finally:
            reset_prompt_ads_user(token)
            ml.CONTENT_RESTRICTION_MODE = prev
            if prev in ("off", ""):
                os.environ["CONTENT_RESTRICTION"] = "off"


class TestAdsTeaseCopy(unittest.TestCase):
    def tearDown(self):
        try:
            reset_prompt_ads_user(bind_prompt_ads_user(is_ads=False))
        except Exception:
            pass

    def test_turn_rules_ads_tease(self):
        token = bind_prompt_ads_user(is_ads=True)
        try:
            rules = chat_rules_block(
                has_leave_intent=False,
                user_input="今晚想做爱",
                affection=0,
                language="zh",
                topic_band="adult",
            )
            self.assertIn("广告户", rules)
            self.assertNotIn("半步", rules)
        finally:
            reset_prompt_ads_user(token)

    def test_enrich_ads(self):
        token = bind_prompt_ads_user(is_ads=True)
        try:
            out = enrich_human_prompt(
                "base",
                user_input="想听你 dirty talk",
                affection=0,
                language="zh",
                topic_band="adult",
            )
            self.assertIn("直白", out)
        finally:
            reset_prompt_ads_user(token)

    def test_rewrite_ads(self):
        token = bind_prompt_ads_user(is_ads=True)
        try:
            out = rewrite_instruction(
                "tease_escape",
                affection=0,
                language="zh",
                user_input="想听黄腔",
            )
            self.assertIn("直白", out)
            self.assertIn("explicitly", out.lower() + out)
        finally:
            reset_prompt_ads_user(token)

    def test_organic_tease_not_ads_copy(self):
        token = bind_prompt_ads_user(is_ads=False)
        try:
            rules = chat_rules_block(
                has_leave_intent=False,
                user_input="今晚想做爱",
                affection=0,
                language="zh",
                topic_band="adult",
            )
            self.assertNotIn("广告户", rules)
            self.assertIn("暧昧档", rules)
        finally:
            reset_prompt_ads_user(token)


if __name__ == "__main__":
    unittest.main()
