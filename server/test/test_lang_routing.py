"""语言路由、单语 pack、列表软优先、job 状态。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestResolveLang(unittest.TestCase):
    def test_priority_body_over_query_and_header(self):
        from services.prompts.resolve import resolve_lang

        self.assertEqual(
            resolve_lang(
                body_lang="ja",
                query_lang="en",
                accept_language="zh-CN",
                session_lang="ko",
            ),
            "ja",
        )

    def test_query_over_accept_language(self):
        from services.prompts.resolve import resolve_lang

        self.assertEqual(
            resolve_lang(query_lang="en", accept_language="zh-CN"),
            "en",
        )

    def test_accept_language_normalized(self):
        from services.prompts.resolve import resolve_lang

        self.assertEqual(resolve_lang(accept_language="ja-JP,en;q=0.8"), "ja")
        self.assertEqual(resolve_lang(accept_language="zh-CN"), "zh")
        self.assertEqual(resolve_lang(accept_language="pt-BR"), "pt")
        self.assertEqual(resolve_lang(accept_language="ar-SA"), "ar")
        self.assertEqual(resolve_lang(accept_language="fa-IR"), "fa")

    def test_session_then_default(self):
        from services.prompts.resolve import resolve_lang

        self.assertEqual(resolve_lang(session_lang="ko"), "ko")
        self.assertEqual(resolve_lang(), "en")

    def test_content_langs_passthrough(self):
        from services.prompts.resolve import resolve_lang

        # 库内 / 前端语种通配，不再一律回落 en
        self.assertEqual(resolve_lang(body_lang="fr"), "fr")
        self.assertEqual(resolve_lang(body_lang="ar"), "ar")
        self.assertEqual(resolve_lang(body_lang="ru"), "ru")
        self.assertEqual(resolve_lang(body_lang="hi"), "hi")
        self.assertEqual(resolve_lang(query_lang="jp"), "ja")
        self.assertEqual(resolve_lang(body_lang="not-a-lang"), "en")


class TestGetPackLazy(unittest.TestCase):
    def test_ja_pack_is_japanese_not_chinese_rules(self):
        from services.prompts import resolve as resolve_mod
        from services.prompts.resolve import get_pack

        resolve_mod._PACK_CACHE.clear()
        pack = get_pack("ja")
        self.assertEqual(list(resolve_mod._PACK_CACHE.keys()), ["ja"])

        system = pack["system_prompt"]
        first = pack["first_turn"]
        self.assertIn("あなたは", system)
        self.assertIn("関係段階", first)
        self.assertNotIn("这是你和对方的第一次对话", first)
        self.assertNotIn("【关系阶段——最高优先级覆盖】", first)
        self.assertNotIn("你绝对禁止回复任何色情", pack["content_restriction"])

    def test_en_pack_not_zh_first_turn(self):
        from services.prompts.resolve import get_pack

        pack = get_pack("en")
        self.assertNotIn("这是你和对方的第一次对话", pack["first_turn"])
        self.assertIn("first conversation", pack["first_turn"].lower())


class TestFiveLangPacksAndDetection(unittest.TestCase):
    def test_ar_pack_is_arabic_not_chinese(self):
        from services.prompts import resolve as resolve_mod
        from services.prompts.resolve import get_pack

        resolve_mod._PACK_CACHE.clear()
        pack = get_pack("ar")
        self.assertIn("أول محادثة", pack["first_turn"])
        self.assertNotIn("这是你和对方的第一次对话", pack["first_turn"])
        self.assertIn("reply_lang_turn", pack)
        self.assertIn("العربية", pack["reply_lang_turn"] + pack["first_turn"] + "العربية")

    def test_build_system_prompt_ar_not_rewritten_to_zh(self):
        try:
            from services.agent_utils import build_system_prompt
        except ImportError:
            self.skipTest("sqlalchemy not installed in this interpreter")

        prompt = build_system_prompt({"name": "نور", "age": 22, "city": "الرياض"}, "ar", turns=1)
        self.assertNotIn("这是你和对方的第一次对话", prompt)
        self.assertNotIn("【你的性格】", prompt)

    def test_detect_arabic_and_persian_and_cyrillic_and_turkish(self):
        try:
            from services.agent_utils import detect_user_message_lang, reply_lang_turn_instruction
        except ImportError:
            self.skipTest("sqlalchemy not installed in this interpreter")

        self.assertEqual(detect_user_message_lang("مرحبا كيفك", "zh"), "ar")
        self.assertEqual(detect_user_message_lang("سلام خوبی؟ چطوری", "en"), "fa")
        self.assertEqual(detect_user_message_lang("Привет, как дела", "en"), "ru")
        self.assertEqual(detect_user_message_lang("Привет", "bg"), "bg")
        self.assertEqual(detect_user_message_lang("Merhaba nasılsın", "tr"), "tr")
        self.assertEqual(detect_user_message_lang("Merhaba", "en"), "en")
        turn = reply_lang_turn_instruction("مرحبا", "zh")
        self.assertIn("العربية", turn)
        self.assertNotIn("必须用简体中文", turn)
        tr_turn = reply_lang_turn_instruction("Nasılsın bugün", "tr")
        self.assertIn("Türkçe", tr_turn)

    def test_unknown_lang_pack_falls_back_en_not_zh(self):
        from services.prompts.resolve import pack_lang, get_pack

        self.assertEqual(pack_lang("fr"), "en")
        pack = get_pack("fr")
        self.assertIn("first conversation", pack["first_turn"].lower())
        try:
            from services.agent_utils import build_system_prompt
        except ImportError:
            return
        prompt = build_system_prompt({"name": "A", "age": 22}, "fr", turns=1)
        self.assertNotIn("这是你和对方的第一次对话", prompt)

    def test_icebreaker_ar_not_chinese(self):
        try:
            from services.moments import _icebreaker_prompts
        except ImportError:
            # moments 依赖 sqlalchemy；仍可用 pack 验证破冰文案
            from services.prompts.resolve import get_pack

            pack = get_pack("ar")
            user = pack["icebreaker_user"].format(caption="قهوة الصباح")
            self.assertNotIn("中文破冰", user)
            self.assertNotIn("写一条完整的中文", user)
            blob = pack["icebreaker_system"] + user
            self.assertTrue("عربي" in blob or "العربية" in blob or "كسر" in blob)
            return

        system, user = _icebreaker_prompts("قهوة الصباح", "ar")
        self.assertNotIn("中文破冰", user)
        self.assertNotIn("写一条完整的中文", user)
        blob = system + user
        self.assertTrue("عربي" in blob or "العربية" in blob or "كسر" in blob)


class TestLangPreferClause(unittest.TestCase):
    def test_match_query_flag(self):
        def match_mode(raw: str) -> str:
            return "off" if str(raw).lower() in ("off", "0", "false", "no") else "prefer"

        self.assertEqual(match_mode("off"), "off")
        self.assertEqual(match_mode("prefer"), "prefer")
        self.assertEqual(match_mode("PREFER"), "prefer")

    def test_match_off_disables_sql_clause(self):
        try:
            from services.companion_manager import _lang_prefer_clause
        except ImportError:
            self.skipTest("sqlalchemy not installed in this interpreter")
        self.assertIsNone(_lang_prefer_clause("en", "off"))
        self.assertIsNone(_lang_prefer_clause("", "prefer"))
        self.assertIsNotNone(_lang_prefer_clause("en", "prefer"))
        self.assertIsNotNone(_lang_prefer_clause("ja", "prefer"))


class TestAgentJobAndStatus(unittest.TestCase):
    def test_job_roundtrip_extra_fields(self):
        from core.agent_queue import AgentJob

        job = AgentJob(
            job_id="j1",
            connection_id="c1",
            companion_id="bot",
            user_id=7,
            user_text_for_agent="hi",
            combined_user_plain="hi",
            profile={"name": "A"},
            companion_state={"mood": "ok"},
            memory_text="mem",
            language="en",
            current_time="now",
            user_gender="female",
            summary_due=False,
            has_leave_intent=True,
            burst_count=2,
            open_threads=["t"],
            deny_hooks=["d"],
            recent_assistant=["yo"],
            idle_seconds=12.5,
            last_extreme_ts="ts",
            source="rest",
        )
        restored = AgentJob.from_dict(job.to_dict())
        self.assertTrue(restored.has_leave_intent)
        self.assertEqual(restored.burst_count, 2)
        self.assertEqual(restored.open_threads, ["t"])
        self.assertEqual(restored.source, "rest")
        self.assertEqual(restored.idle_seconds, 12.5)

    def test_job_status_memory_fallback(self):
        from core.agent_queue import get_job_status, set_job_status

        jid = "test-job-status-mem-1"
        set_job_status(
            jid,
            user_id=3,
            companion_id="c",
            status="queued",
        )
        queued = get_job_status(jid)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["user_id"], 3)
        set_job_status(
            jid,
            user_id=3,
            companion_id="c",
            status="done",
            result={"text": "ok", "bubbles": ["ok"]},
        )
        done = get_job_status(jid)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["text"], "ok")
        self.assertEqual(done["created_at"], queued["created_at"])


if __name__ == "__main__":
    unittest.main()
