"""快速回复：匹配 / 关键词归一 / 配置门禁。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://trandsai:trandsai@127.0.0.1:5432/trandsai"

from services.quick_reply import (
    _match_keyword,
    library_to_dict,
    normalize_keywords,
    pick_quick_reply,
)


class TestNormalizeKeywords(unittest.TestCase):
    def test_list(self):
        self.assertEqual(normalize_keywords([" 哈哈 ", "笑死"]), ["哈哈", "笑死"])

    def test_csv(self):
        self.assertEqual(normalize_keywords("哈哈,笑死，嘿"), ["哈哈", "笑死", "嘿"])

    def test_empty(self):
        self.assertEqual(normalize_keywords([]), [])
        self.assertEqual(normalize_keywords(None), [])


class TestMatchKeyword(unittest.TestCase):
    def test_contains_casefold(self):
        self.assertTrue(_match_keyword("哈哈我也是", ["哈哈"]))
        self.assertTrue(_match_keyword("Hello WORLD", ["world"]))
        self.assertFalse(_match_keyword("你好", ["哈哈"]))


class TestPickQuickReply(unittest.TestCase):
    def test_prefers_keyword_over_catchall(self):
        kw = SimpleNamespace(
            id=1,
            companion_ids=["abc12345"],
            language="zh",
            content="关键词回",
            keywords=["哈哈"],
            sort_order=1,
            is_active=True,
            created_by="",
            created_at=None,
            updated_at=None,
        )
        catch = SimpleNamespace(
            id=2,
            companion_ids=["abc12345"],
            language="zh",
            content="兜底回",
            keywords=[],
            sort_order=0,
            is_active=True,
            created_by="",
            created_at=None,
            updated_at=None,
        )

        class _LibQ:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [kw, catch]

        class _CfgQ:
            def filter(self, *a, **k):
                return self

            def first(self):
                return SimpleNamespace(is_active=True, companion_id="abc12345")

        class _DB:
            def __init__(self):
                self._n = 0

            def query(self, model):
                name = getattr(model, "__name__", str(model))
                if "Config" in name:
                    return _CfgQ()
                return _LibQ()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("services.quick_reply.get_db", return_value=_DB()):
            with mock.patch("services.quick_reply._get_last_used_id", return_value=None):
                with mock.patch("services.quick_reply._set_last_used_id"):
                    got = pick_quick_reply(
                        companion_id="abc12345",
                        language="zh",
                        user_text="哈哈今天真好",
                        user_id=1,
                    )
        self.assertEqual(got["content"], "关键词回")

    def test_disabled_config_skips(self):
        class _CfgQ:
            def filter(self, *a, **k):
                return self

            def first(self):
                return SimpleNamespace(is_active=False, companion_id="abc12345")

        class _DB:
            def query(self, *a, **k):
                return _CfgQ()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("services.quick_reply.get_db", return_value=_DB()):
            got = pick_quick_reply(
                companion_id="abc12345",
                language="zh",
                user_text="哈哈",
                user_id=1,
            )
        self.assertIsNone(got)

    def test_no_config_still_picks_keyword(self):
        kw = SimpleNamespace(
            id=2,
            companion_ids=["ff19266b"],
            language="en",
            content="猴哥在此 恭喜你命中了",
            keywords=["猴哥在吗"],
            sort_order=1,
            is_active=True,
            created_by="",
            created_at=None,
            updated_at=None,
        )

        class _LibQ:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [kw]

        class _CfgQ:
            def filter(self, *a, **k):
                return self

            def first(self):
                return None

        class _DB:
            def query(self, model):
                name = getattr(model, "__name__", str(model))
                if "Config" in name:
                    return _CfgQ()
                return _LibQ()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("services.quick_reply.get_db", return_value=_DB()):
            with mock.patch("services.quick_reply._get_last_used_id", return_value=None):
                with mock.patch("services.quick_reply._set_last_used_id"):
                    got = pick_quick_reply(
                        companion_id="ff19266b",
                        language="zh",
                        user_text="你好，猴哥在吗",
                        user_id=1,
                    )
        self.assertEqual(got["content"], "猴哥在此 恭喜你命中了")

    def test_keyword_hits_across_language(self):
        """用户语种是 zh 时，en 库里的中文触发词仍要命中，不能被同智能体的 zh 兜底抢走。"""
        kw = SimpleNamespace(
            id=2,
            companion_ids=["ff19266b"],
            language="en",
            content="猴哥在此 恭喜你命中了",
            keywords=["猴哥在吗"],
            sort_order=1,
            is_active=True,
            created_by="",
            created_at=None,
            updated_at=None,
        )
        catch = SimpleNamespace(
            id=3,
            companion_ids=["ff19266b"],
            language="zh",
            content="兜底不该出",
            keywords=[],
            sort_order=0,
            is_active=True,
            created_by="",
            created_at=None,
            updated_at=None,
        )

        class _LibQ:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [kw, catch]

        class _CfgQ:
            def filter(self, *a, **k):
                return self

            def first(self):
                return None

        class _DB:
            def query(self, model):
                name = getattr(model, "__name__", str(model))
                if "Config" in name:
                    return _CfgQ()
                return _LibQ()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("services.quick_reply.get_db", return_value=_DB()):
            with mock.patch("services.quick_reply._get_last_used_id", return_value=None):
                with mock.patch("services.quick_reply._set_last_used_id"):
                    got = pick_quick_reply(
                        companion_id="ff19266b",
                        language="zh",
                        user_text="喂，猴哥在吗",
                        user_id=1,
                    )
        self.assertEqual(got["content"], "猴哥在此 恭喜你命中了")

    def test_to_dict_shape(self):
        row = SimpleNamespace(
            id=9,
            companion_ids=["abc12345"],
            language="en",
            content="hi",
            keywords=["hey"],
            sort_order=3,
            is_active=True,
            created_by="admin",
            created_at=None,
            updated_at=None,
        )
        d = library_to_dict(row, name_map={"abc12345": "Bot"})
        self.assertEqual(d["id"], 9)
        self.assertEqual(d["keywords"], ["hey"])
        self.assertEqual(d["companions"][0]["name"], "Bot")
        self.assertTrue(d["is_active"])


if __name__ == "__main__":
    unittest.main()
