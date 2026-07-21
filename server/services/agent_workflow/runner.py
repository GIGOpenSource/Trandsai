"""Agent runner facade — routes v1/v2 without breaking legacy imports."""
from __future__ import annotations

from services.agent_workflow.state import TurnContext, TurnResult
from services.agent_runner import run_agent, run_memory_update_async

__all__ = ["TurnContext", "TurnResult", "run_agent", "run_memory_update_async", "AgentRunner"]


class AgentRunner:
    def run(self, ctx: TurnContext) -> TurnResult:
        data = run_agent(
            user_input=ctx.user_input,
            profile=ctx.profile,
            companion_state=ctx.companion_state,
            memory_text=ctx.memory_text,
            knowledge_text=ctx.knowledge_text,
            language=ctx.language,
            current_time=ctx.current_time,
            user_gender=ctx.user_gender,
            summary_due=ctx.summary_due,
            extract_due=ctx.extract_due,
        )
        return TurnResult.from_run_dict(data)
