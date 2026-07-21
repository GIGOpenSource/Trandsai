"""Reuse Chroma PersistentClient instances per persist directory (S-18)."""
from __future__ import annotations

import threading
from typing import Dict, Tuple

import chromadb
from chromadb.config import Settings

_lock = threading.Lock()
_clients: Dict[str, chromadb.PersistentClient] = {}


def get_persistent_client(persist_dir: str) -> chromadb.PersistentClient:
    with _lock:
        client = _clients.get(persist_dir)
        if client is None:
            client = chromadb.PersistentClient(
                path=persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            _clients[persist_dir] = client
        return client


def evict_client(persist_dir: str) -> None:
    """Drop the cached client (e.g. before deleting/recreating the persist dir)."""
    with _lock:
        _clients.pop(persist_dir, None)
