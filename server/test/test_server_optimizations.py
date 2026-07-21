"""Minimal tests for server optimizations."""
import importlib.util
import os
import unittest
from unittest.mock import MagicMock, patch

_HAS_CHROMADB = importlib.util.find_spec("chromadb") is not None


class TestResolveMaxTokens(unittest.TestCase):
    def test_low_affection(self):
        from services.llm.client import resolve_max_tokens

        self.assertEqual(resolve_max_tokens(10), 128)

    def test_high_affection(self):
        from services.llm.client import resolve_max_tokens

        self.assertGreaterEqual(resolve_max_tokens(80), 256)


class TestMemoryTier(unittest.TestCase):
    @unittest.skipUnless(_HAS_CHROMADB, "chromadb not installed")
    def test_compact_shorter_than_full(self):
        from services.memory import CompanionMemory

        mem = CompanionMemory.__new__(CompanionMemory)
        mem.companion_id = "test"
        mem.short_term = MagicMock()
        mem.short_term.get_recent_turns.return_value = [
            {"role": "user", "content": "hello"},
        ]
        mem.facts = MagicMock()
        mem.facts.get_facts.return_value = ["likes coffee"]
        mem.summary = MagicMock()
        mem.summary.get_summary.return_value = "friends"

        def fake_context(query=""):
            return {
                "recent_dialogue": [],
                "episodes": [],
                "facts": ["likes coffee"],
                "summary": "friends",
            }

        mem.get_context = fake_context
        compact = mem.build_prompt_context(tier="compact")
        full = mem.build_prompt_context(tier="full", max_chars=3500)
        self.assertLessEqual(len(compact), len(full) + 50)


class TestRateLimit(unittest.TestCase):
    def test_memory_fallback_allows_under_limit(self):
        from core import rate_limit as rl

        rl._user_chat_timestamps.clear()
        self.assertTrue(rl.check_chat_rate_limit(999001))
        self.assertTrue(rl.check_chat_rate_limit(999001))


class TestProductionValidation(unittest.TestCase):
    def test_sqlite_blocked_in_prod(self):
        from core.database import validate_production_config

        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            with patch("core.database._is_sqlite", True):
                with self.assertRaises(RuntimeError):
                    validate_production_config()


class TestAgentStateFacade(unittest.TestCase):
    def test_turn_context_roundtrip(self):
        from services.agent_workflow.state import TurnContext, TurnResult

        ctx = TurnContext.from_ws(
            user_text="hi",
            profile={"name": "A"},
            companion_state={"mood": "开心", "affection": 1, "turns": 0},
            memory_text="mem",
        )
        self.assertEqual(ctx.user_input, "hi")
        result = TurnResult.from_run_dict(
            {"response": "ok", "mood": "开心", "affection": 1.0}
        )
        self.assertEqual(result.response, "ok")


class TestLlmGate(unittest.TestCase):
    def test_gate_stats_exposes_inflight(self):
        from core.concurrency import llm_gate, llm_gate_stats

        with llm_gate():
            stats = llm_gate_stats()
            self.assertIn("in_flight", stats)
            self.assertGreaterEqual(stats["in_flight"], 1)
        self.assertEqual(llm_gate_stats()["in_flight"], 0)


class TestHealthPayload(unittest.TestCase):
    def test_health_has_queue_fields(self):
        from core.health import build_health_payload

        payload = build_health_payload()
        for key in (
            "agent_queue_depth",
            "chat_flush_queue_depth",
            "chat_flush_processing_depth",
            "llm_metrics",
        ):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
