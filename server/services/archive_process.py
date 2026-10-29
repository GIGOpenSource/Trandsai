"""C4/D6：消费档案队列任务（Facts/Summary/Evolve），可用 flash/本地。"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def process_archive_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """同步处理一条档案任务；失败仅打日志。"""
    kind = (job or {}).get("kind") or "facts"
    try:
        if kind == "facts":
            from services.agent import extract_facts_node

            return extract_facts_node({
                "user_input": job.get("user_input") or "",
                "final_response": job.get("final_response") or "",
                "language": job.get("language") or "zh",
                "extract_due": True,
            })
        if kind == "summary":
            from services.agent import summary_node

            return summary_node({
                "user_input": job.get("user_input") or "",
                "final_response": job.get("final_response") or "",
                "language": job.get("language") or "zh",
                "summary_due": True,
                "state": job.get("state") or {"summary": job.get("old_summary") or ""},
            })
        if kind == "evolve":
            from services.agent import persona_evolve_node

            return persona_evolve_node({
                "user_input": job.get("user_input") or "",
                "final_response": job.get("final_response") or "",
                "language": job.get("language") or "zh",
                "profile": job.get("profile") or {},
                "state": job.get("state") or {},
                "memory_text": job.get("memory_text") or "",
            })
        logger.warning("unknown archive kind: %s", kind)
        return {}
    except Exception as e:
        logger.warning("archive job %s failed: %s", kind, e)
        return {"error": str(e)}
