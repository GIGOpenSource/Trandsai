"""HTTP 访问日志：写本地文件，按大小切割。不做库表、不记请求体。"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode

_LOCK = threading.Lock()
_CURRENT_NAME = "api.log"
_FLAG_NAME = ".enabled"
_NAME_RE = re.compile(r"^api(?:\.\d{8}-\d{6}(?:\.\d+)?)?\.log$")
_FLAG_CACHE = {"path": None, "mtime": None, "value": None}
_SECRET_QS = frozenset(
    {"token", "x-token", "password", "authorization", "access_token", "refresh_token"}
)
_SKIP_PREFIXES = (
    "/health",
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/data/images",
    "/api/admin/logs",
)


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_forced_off() -> bool:
    raw = (os.getenv("API_LOG_ENABLED") or "1").strip().lower()
    return raw in ("0", "false", "no", "off")


def _flag_path() -> Path:
    return log_dir() / _FLAG_NAME


def _read_runtime_flag() -> Optional[bool]:
    path = _flag_path()
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        _FLAG_CACHE["path"] = key
        _FLAG_CACHE["mtime"] = None
        _FLAG_CACHE["value"] = None
        return None
    if _FLAG_CACHE["path"] == key and _FLAG_CACHE["mtime"] == st.st_mtime:
        return _FLAG_CACHE["value"]
    try:
        raw = path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    value = raw not in ("0", "false", "no", "off", "")
    _FLAG_CACHE["path"] = key
    _FLAG_CACHE["mtime"] = st.st_mtime
    _FLAG_CACHE["value"] = value
    return value


def enabled() -> bool:
    if env_forced_off():
        return False
    flag = _read_runtime_flag()
    if flag is None:
        return True
    return flag


def set_enabled(on: bool) -> bool:
    """后台开关。环境变量 API_LOG_ENABLED=0 时强制关闭，无法打开。"""
    if on and env_forced_off():
        return False
    directory = log_dir()
    _ensure_dir(directory)
    path = _flag_path()
    path.write_text("1\n" if on else "0\n", encoding="utf-8")
    try:
        _FLAG_CACHE["path"] = str(path)
        _FLAG_CACHE["mtime"] = path.stat().st_mtime
    except OSError:
        _FLAG_CACHE["mtime"] = None
    _FLAG_CACHE["value"] = bool(on) and not env_forced_off()
    return enabled()


def max_bytes() -> int:
    mb = max(1, _env_int("API_LOG_MAX_MB", 20))
    return mb * 1024 * 1024


def backup_count() -> int:
    return max(1, _env_int("API_LOG_BACKUP_COUNT", 20))


def log_dir() -> Path:
    raw = (os.getenv("API_LOG_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    # Docker: ./data -> /app/data；本地：server/data/logs/api
    server_root = Path(__file__).resolve().parent.parent
    return server_root / "data" / "logs" / "api"


def current_path() -> Path:
    return log_dir() / _CURRENT_NAME


def should_skip(path: str) -> bool:
    p = path or ""
    return any(p == prefix or p.startswith(prefix + "/") for prefix in _SKIP_PREFIXES) or p in _SKIP_PREFIXES


def client_ip(headers: dict, client_host: Optional[str] = None) -> str:
    xff = (headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip() or "-"
    real = (headers.get("x-real-ip") or headers.get("X-Real-IP") or "").strip()
    if real:
        return real
    return (client_host or "").strip() or "-"


def sanitize_query(query: str) -> str:
    if not query:
        return ""
    pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key.lower() in _SECRET_QS:
            pairs.append((key, "***"))
        else:
            pairs.append((key, value))
    text = urlencode(pairs, doseq=True)
    if len(text) > 500:
        return text[:500] + "..."
    return text


def format_line(
    method: str,
    path: str,
    status: int,
    elapsed_ms: float,
    ip: str = "-",
    uid: str = "-",
    query: str = "",
    when: Optional[datetime] = None,
) -> str:
    ts = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qs = sanitize_query(query)
    suffix = f" ?{qs}" if qs else ""
    return (
        f"{ts} {int(status)} {method} {path}{suffix} "
        f"{elapsed_ms:.1f}ms ip={ip or '-'} uid={uid or '-'}\n"
    )


def _ensure_dir(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)


def _rotated_files(directory: Path) -> list[Path]:
    items = [p for p in directory.glob("api.*.log") if p.is_file() and p.name != _CURRENT_NAME]
    items.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return items


def _prune(directory: Path, keep: int) -> None:
    extra = _rotated_files(directory)
    overflow = len(extra) - keep
    if overflow <= 0:
        return
    for path in extra[:overflow]:
        try:
            path.unlink()
        except OSError:
            pass


def _rotate(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"api.{stamp}.log")
    n = 0
    while dest.exists():
        n += 1
        dest = path.with_name(f"api.{stamp}.{n}.log")
    try:
        path.rename(dest)
    except OSError:
        return
    _prune(path.parent, backup_count())


def write_line(line: str) -> None:
    if not enabled():
        return
    directory = log_dir()
    path = directory / _CURRENT_NAME
    text = line if line.endswith("\n") else line + "\n"
    with _LOCK:
        try:
            _ensure_dir(directory)
            encoded_len = len(text.encode("utf-8"))
            if path.exists() and path.stat().st_size + encoded_len >= max_bytes():
                _rotate(path)
            with path.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(text)
        except OSError:
            return


def write_access(
    method: str,
    path: str,
    status: int,
    elapsed_ms: float,
    ip: str = "-",
    uid: str = "-",
    query: str = "",
) -> None:
    if not enabled() or should_skip(path):
        return
    write_line(format_line(method, path, status, elapsed_ms, ip=ip, uid=uid, query=query))


def resolve_name(name: Optional[str]) -> Path:
    raw = (name or _CURRENT_NAME).strip() or _CURRENT_NAME
    if raw != Path(raw).name or not _NAME_RE.match(raw):
        raise ValueError("invalid log file name")
    directory = log_dir().resolve()
    path = (directory / raw).resolve()
    if path.parent != directory:
        raise ValueError("invalid log file name")
    return path


def list_files() -> list[dict]:
    directory = log_dir()
    if not directory.exists():
        return []
    rows = []
    for path in directory.iterdir():
        if not path.is_file() or not _NAME_RE.match(path.name):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        rows.append(
            {
                "name": path.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "current": path.name == _CURRENT_NAME,
            }
        )
    current = [r for r in rows if r["current"]]
    others = [r for r in rows if not r["current"]]
    others.sort(key=lambda r: r["mtime"], reverse=True)
    return current + others


def iter_allowed_names(names: Iterable[str]) -> list[str]:
    return [n for n in names if _NAME_RE.match(n)]


def delete_file(name: str) -> bool:
    path = resolve_name(name)
    if not path.is_file():
        return False
    path.unlink()
    return True


def delete_all() -> int:
    deleted = 0
    for item in list_files():
        try:
            if delete_file(item["name"]):
                deleted += 1
        except (ValueError, OSError):
            continue
    return deleted


def settings() -> dict:
    files = list_files()
    return {
        "enabled": enabled(),
        "env_forced_off": env_forced_off(),
        "dir": str(log_dir()),
        "current": _CURRENT_NAME,
        "max_mb": max(1, max_bytes() // (1024 * 1024)),
        "backup_count": backup_count(),
        "files": files,
    }
