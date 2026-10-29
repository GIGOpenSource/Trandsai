"""KnowledgeBase search_entries smoke tests (mocked Chroma / embedding)."""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _install_stubs():
    if "core.chroma_client" not in sys.modules:
        m = types.ModuleType("core.chroma_client")
        m.get_persistent_client = MagicMock()
        m.evict_client = MagicMock()
        sys.modules["core.chroma_client"] = m
    if "core.database" not in sys.modules:
        m = types.ModuleType("core.database")
        m.KnowledgeEntryORM = MagicMock()
        m.get_db = MagicMock()
        sys.modules["core.database"] = m
    if "services.memory" not in sys.modules:
        m = types.ModuleType("services.memory")
        m.get_embedding = MagicMock(return_value=[0.1, 0.2])
        sys.modules["services.memory"] = m


class KnowledgeSearchFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        chroma = sys.modules["core.chroma_client"]
        client = MagicMock()
        collection = MagicMock()
        client.get_or_create_collection.return_value = collection
        chroma.get_persistent_client.return_value = client
        from services import knowledge_base as kb_mod

        cls.kb_mod = kb_mod

    def _kb(self):
        collection = MagicMock()
        collection.count.return_value = 2
        collection.query.return_value = {
            "ids": [["a1", "a2"]],
            "documents": [["doc-one", "doc-two"]],
            "metadatas": [[
                {"title": "T1", "category": "other", "language": "zh", "source": "manual"},
                {"title": "T2", "category": "other", "language": "en", "source": "manual"},
            ]],
            "distances": [[0.1, 0.2]],
        }
        kb = object.__new__(self.kb_mod.KnowledgeBase)
        kb.collection = collection
        return kb, collection

    def test_search_entries_returns_hits(self):
        kb, collection = self._kb()
        with patch.object(self.kb_mod, "get_embedding", return_value=[0.1, 0.2, 0.3]):
            items = kb.search_entries("社交礼仪", top_k=2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "T1")
        self.assertEqual(items[0]["content"], "doc-one")
        collection.query.assert_called_once()

    def test_search_entries_empty_when_no_embedding(self):
        kb, _ = self._kb()
        with patch.object(self.kb_mod, "get_embedding", return_value=None):
            self.assertEqual(kb.search_entries("x"), [])

    def test_search_entries_empty_collection(self):
        kb, collection = self._kb()
        collection.count.return_value = 0
        with patch.object(self.kb_mod, "get_embedding", return_value=[0.1]):
            self.assertEqual(kb.search_entries("x"), [])


if __name__ == "__main__":
    unittest.main()
