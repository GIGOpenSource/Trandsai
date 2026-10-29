from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _lock_dir() -> Path:
    base = Path(tempfile.gettempdir()) / "trandsai"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _normalize_lock_path(name_or_path: str) -> Path:
    raw = str(name_or_path or "").strip()
    if not raw:
        raise ValueError("lock name/path is required")
    candidate = Path(raw)
    if candidate.is_absolute():
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    safe_name = raw.replace("\\", "_").replace("/", "_")
    return _lock_dir() / safe_name


def acquire_process_lock(name_or_path: str):
    """Best-effort cross-platform file lock.

    Returns an open file object when the lock is acquired, otherwise None.
    Keep the returned file handle alive for as long as the lock is needed.
    """
    lock_path = _normalize_lock_path(name_or_path)
    fd = open(lock_path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        fd.close()
        return None
