"""对话链路多语言文案：按语言从 pack 取，一次只加载一份。"""
from __future__ import annotations

from typing import Dict

from services.prompts.resolve import get_pack


def stage_text(stage: str, language: str = "zh") -> str:
    table = get_pack(language)["stage"]
    return table.get(stage or "stranger", table["stranger"])


def respond_ui(language: str = "zh") -> Dict[str, str]:
    return get_pack(language)["respond_ui"]


def quality_ui(language: str = "zh") -> Dict[str, str]:
    return get_pack(language)["quality_ui"]


def input_understanding_rule(language: str = "zh") -> str:
    return get_pack(language)["input_understanding"]
