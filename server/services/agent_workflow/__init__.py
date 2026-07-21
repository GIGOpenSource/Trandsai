from services.agent_workflow.state import TurnContext, TurnResult

__all__ = ["AgentRunner", "TurnContext", "TurnResult"]


def __getattr__(name: str):
    if name == "AgentRunner":
        from services.agent_workflow.runner import AgentRunner

        return AgentRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
