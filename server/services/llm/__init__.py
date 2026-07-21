__all__ = ["llm_invoke", "resolve_max_tokens", "record", "metrics_snapshot"]


def __getattr__(name: str):
    if name in ("llm_invoke", "resolve_max_tokens"):
        from services.llm.client import llm_invoke, resolve_max_tokens

        return llm_invoke if name == "llm_invoke" else resolve_max_tokens
    if name == "record":
        from services.llm.token_meter import record

        return record
    if name == "metrics_snapshot":
        from services.llm.token_meter import snapshot as metrics_snapshot

        return metrics_snapshot
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
