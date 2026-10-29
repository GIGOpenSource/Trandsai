"""提示词兼容层：按语言从 packs 懒加载，禁止启动时装入七语全文。"""
from __future__ import annotations

from typing import Any

from services.prompts.resolve import SUPPORTED_LANGS, get_pack, resolve_lang

_BANNED_WORDS = [
    "AI",
    "模型",
    "虚拟",
    "程序",
    "算法",
    "机器人",
    "claude",
    "grok",
    "assistant",
    "language model",
    "人工知能",
    "ロボット",
    "가상",
    "인공지능",
]


class _LangKeyed:
    """dict-like：按 lang 取 pack 中某一字段，兼容旧的 `_FOO.get(lang, _FOO['zh'])`。"""

    def __init__(self, field: str):
        self._field = field

    def __getitem__(self, lang: str) -> Any:
        return get_pack(lang)[self._field]

    def get(self, lang: str, default: Any = None) -> Any:
        pack = get_pack(lang)
        if self._field in pack:
            return pack[self._field]
        return default if default is not None else get_pack("en")[self._field]

    def __contains__(self, lang: object) -> bool:
        return str(lang) in SUPPORTED_LANGS


_SYSTEM_PROMPTS = _LangKeyed("system_prompt")
_HUMANIZE_CONFIG = _LangKeyed("humanize")
_GENDER_MAP = _LangKeyed("gender_map")
_SEXUAL_ORIENTATION_TEXTS = _LangKeyed("orientation_texts")
_CONTENT_RESTRICTIONS = _LangKeyed("content_restriction")
_FULL_INTIMACY_GUIDANCE = _LangKeyed("full_intimacy_guidance")
_ADULT_LEGAL_FLOOR = _LangKeyed("adult_legal_floor")
_ADULT_INTIMACY_BANDS = _LangKeyed("adult_intimacy_bands")


def adult_intimacy_guidance(language: str, affection: float) -> str:
    """随亲密度提升成人对话接受度（默认 CONTENT_RESTRICTION=off 时注入）。

    affection 应为 effective_prompt_affection(...) 结果（含全局默认档）。
    产品偏好：低亲密度也走「暧昧接住」档，避免试探档把场拉开。
    档位见各语 pack.adult_intimacy_bands：约 <22 / <50 / <80 / >=80。

    广告户：跳过档位梯子，用直白 dirty-talk 指引；法律底线只留未成年人一句。
    """
    from services.affection_floor import is_prompt_ads_user

    pack = get_pack(language)
    if is_prompt_ads_user():
        body = str(
            pack.get("adult_ads_unrestricted")
            or (pack["adult_intimacy_bands"][-1][1] if pack.get("adult_intimacy_bands") else "")
        ).strip()
        floor = str(pack.get("adult_legal_floor_ads") or "").strip()
        if not floor:
            floor = "勿涉及未成年人。" if str(language or "").lower().startswith("zh") else "Do not involve minors."
        return f"{body}\n{floor}" if body else floor

    aff = float(affection or 0)
    bands = pack["adult_intimacy_bands"]
    # 跳过最冷的试探档：aff<50 直接用暧昧档；再往上按原阈值
    if aff < 50 and len(bands) >= 2:
        body = bands[1][1]
    else:
        body = bands[0][1]
        for threshold, text in bands:
            body = text
            if aff < threshold:
                break
    return f"{body}\n{pack['adult_legal_floor']}"
