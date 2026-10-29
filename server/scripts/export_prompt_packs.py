"""一次性把现有七语文案拆成 services/prompts/packs/{lang}.py。

在尚未改写 agent_prompts / dialogue_i18n 之前运行。
"""
from __future__ import annotations

import ast
import pprint
from pathlib import Path

from services.agent_prompts import (
    _ADULT_INTIMACY_BANDS,
    _ADULT_LEGAL_FLOOR,
    _CONTENT_RESTRICTIONS,
    _FULL_INTIMACY_GUIDANCE,
    _GENDER_MAP,
    _HUMANIZE_CONFIG,
    _SEXUAL_ORIENTATION_TEXTS,
    _SYSTEM_PROMPTS,
)
from services.dialogue_i18n import (
    _INPUT_UNDERSTANDING,
    _QUALITY,
    _RESPOND_UI,
    _STAGE,
)

LANGS = ("zh", "en", "ja", "ko", "pt", "es", "id")

USER_GENDER_DESC = {
    "zh": {"male": "男生", "female": "女生", "secret": "性别保密的人", "": "一个让你心动的人"},
    "en": {"male": "a male", "female": "a female", "secret": "someone who keeps their gender private", "": "someone who makes your heart flutter"},
    "ja": {"male": "男性", "female": "女性", "secret": "性別を秘密にしている人", "": "あなたの心を動かす人"},
    "ko": {"male": "남성", "female": "여성", "secret": "성별을 비밀로 하는 사람", "": "너의 마음을 설레게 하는 사람"},
    "pt": {"male": "um homem", "female": "uma mulher", "secret": "alguém que mantém o gênero em segredo", "": "alguém que faz seu coração acelerar"},
    "es": {"male": "un chico", "female": "una chica", "secret": "alguien que guarda su género en privado", "": "alguien que te hace latir el corazón"},
    "id": {"male": "seorang pria", "female": "seorang wanita", "secret": "seseorang yang merahasiakan gendernya", "": "seseorang yang membuat hatimu berdegup"},
}

USER_GENDER_PRONOUNS = {
    "zh": {"male": ("他", "他"), "female": ("她", "她"), "secret": ("TA", "TA")},
    "en": {"male": ("him", "him"), "female": ("her", "her"), "secret": ("them", "them")},
    "ja": {"male": ("彼", "彼"), "female": ("彼女", "彼女"), "secret": ("相手", "相手")},
    "ko": {"male": ("그", "그"), "female": ("그녀", "그녀"), "secret": ("상대", "상대")},
    "pt": {"male": ("ele", "ele"), "female": ("ela", "ela"), "secret": ("a pessoa", "a pessoa")},
    "es": {"male": ("él", "él"), "female": ("ella", "ella"), "secret": ("la persona", "la persona")},
    "id": {"male": ("dia", "dia"), "female": ("dia", "dia"), "secret": ("orang itu", "orang itu")},
}


def _load_utils_dicts() -> dict:
    src = Path(__file__).resolve().parents[1] / "services" / "agent_utils.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    wanted = {
        "_FIRST_TURN_OVERRIDES",
        "_OUTPUT_LANGUAGE_RULE",
        "_DIALOGUE_TIME_CONTEXT_RULE",
        "_PAREN_THINKING_FORMAT_RULE",
    }
    out = {}
    for node in tree.body:
        name = None
        value = None
        if isinstance(node, ast.Assign) and node.targets:
            t0 = node.targets[0]
            if isinstance(t0, ast.Name) and t0.id in wanted:
                name, value = t0.id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in wanted and node.value is not None:
                name, value = node.target.id, node.value
        if name and value is not None:
            out[name] = ast.literal_eval(value)
    missing = wanted - set(out)
    if missing:
        raise RuntimeError(f"missing agent_utils dicts: {missing}")
    return out


_UTILS = _load_utils_dicts()
_FIRST_TURN_OVERRIDES = _UTILS["_FIRST_TURN_OVERRIDES"]
_OUTPUT_LANGUAGE_RULE = _UTILS["_OUTPUT_LANGUAGE_RULE"]
_DIALOGUE_TIME_CONTEXT_RULE = _UTILS["_DIALOGUE_TIME_CONTEXT_RULE"]
_PAREN_THINKING_FORMAT_RULE = _UTILS["_PAREN_THINKING_FORMAT_RULE"]


def build_pack(lang: str) -> dict:
    return {
        "system_prompt": _SYSTEM_PROMPTS[lang],
        "humanize": _HUMANIZE_CONFIG[lang],
        "first_turn": _FIRST_TURN_OVERRIDES[lang],
        "output_language_rule": _OUTPUT_LANGUAGE_RULE[lang],
        "time_rule": _DIALOGUE_TIME_CONTEXT_RULE[lang],
        "paren_rule": _PAREN_THINKING_FORMAT_RULE[lang],
        "stage": _STAGE[lang],
        "respond_ui": _RESPOND_UI[lang],
        "quality_ui": _QUALITY[lang],
        "input_understanding": _INPUT_UNDERSTANDING[lang],
        "gender_map": _GENDER_MAP[lang],
        "user_gender_pronouns": USER_GENDER_PRONOUNS[lang],
        "user_gender_desc": USER_GENDER_DESC[lang],
        "orientation_texts": _SEXUAL_ORIENTATION_TEXTS[lang],
        "content_restriction": _CONTENT_RESTRICTIONS[lang],
        "full_intimacy_guidance": _FULL_INTIMACY_GUIDANCE[lang],
        "adult_legal_floor": _ADULT_LEGAL_FLOOR[lang],
        "adult_intimacy_bands": _ADULT_INTIMACY_BANDS[lang],
    }


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "services" / "prompts" / "packs"
    out_dir.mkdir(parents=True, exist_ok=True)
    printer = pprint.PrettyPrinter(width=100, indent=4)
    for lang in LANGS:
        pack = build_pack(lang)
        body = printer.pformat(pack)
        path = out_dir / f"{lang}.py"
        path.write_text(
            f'"""单语提示词包：{lang}。由 scripts/export_prompt_packs.py 生成，语义与拆包前一致。"""\n'
            "from __future__ import annotations\n\n"
            f"PACK = {body}\n",
            encoding="utf-8",
        )
        print("wrote", path.name, "bytes", path.stat().st_size)


if __name__ == "__main__":
    main()
