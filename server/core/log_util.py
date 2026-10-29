"""应用日志：同时写 stdout（Docker 可见）与 logging。"""
from __future__ import annotations

import logging
import sys
from typing import Any

_PIPELINE = logging.getLogger("trandsai.pipeline")


def configure_app_logging() -> None:
    """uvicorn 默认只打 access 日志；此处把业务 logger 也输出到 stdout。"""
    import os

    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = (os.getenv("LOG_FORMAT") or "%(levelname)s %(name)s: %(message)s").strip()
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout, force=True)
    for name in ("trandsai.pipeline", "api", "services", "core"):
        logging.getLogger(name).setLevel(level)


def pipeline_log(msg: str, *args: Any) -> None:
    """关键链路日志：print + logger，确保 docker logs 一定能看到。"""
    text = msg % args if args else msg
    print(text, flush=True)
    _PIPELINE.info(text)
