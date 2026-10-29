"""Agent workflow types (M1/M2 facade)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TurnContext:
    user_input: str
    profile: dict[str, Any]
    companion_state: dict[str, Any]
    memory_text: str
    language: str = "zh"
    current_time: str = ""
    user_gender: str = ""
    knowledge_text: str = ""
    summary_due: bool = False
    extract_due: bool = True

    @classmethod
    def from_ws(
        cls,
        *,
        user_text: str,
        profile: dict,
        companion_state: dict,
        memory_text: str,
        language: str = "zh",
        current_time: str = "",
        user_gender: str = "",
        summary_due: bool = False,
        extract_due: bool = True,
    ) -> "TurnContext":
        return cls(
            user_input=user_text,
            profile=profile,
            companion_state=companion_state,
            memory_text=memory_text,
            language=language,
            current_time=current_time,
            user_gender=user_gender,
            summary_due=summary_due,
            extract_due=extract_due,
        )


@dataclass
class TurnResult:
    response: str
    mood: str
    affection: float
    think_result: str = ""
    new_facts: list[str] = field(default_factory=list)
    new_summary: str = ""
    evolved_personality: str = ""
    evolved_background: str = ""
    evolved_speech_style: str = ""
    memory_snapshot: Optional[dict[str, Any]] = None

    @classmethod
    def from_run_dict(cls, data: dict) -> "TurnResult":
        return cls(
            response=data.get("response", ""),
            mood=data.get("mood", "开心"),
            affection=float(data.get("affection", 0)),
            think_result=data.get("think_result", ""),
            new_facts=list(data.get("new_facts") or []),
            new_summary=data.get("new_summary", ""),
            evolved_personality=data.get("evolved_personality", ""),
            evolved_background=data.get("evolved_background", ""),
            evolved_speech_style=data.get("evolved_speech_style", ""),
            memory_snapshot=data.get("memory_snapshot"),
        )
