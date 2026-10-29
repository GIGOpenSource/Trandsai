"""Companion gender stores 男/女/保密; user gender stores male|female|non-binary|confidential."""

from typing import Optional

from core.i18n import normalize_ui_language

_STORED = {
    "male": "男",
    "m": "男",
    "man": "男",
    "男": "男",
    "female": "女",
    "f": "女",
    "woman": "女",
    "女": "女",
    "secret": "保密",
    "other": "保密",
    "unknown": "保密",
    "confidential": "保密",
    "保密": "保密",
    # 多语言展示文案 → 落库
    "masculino": "男",
    "femenino": "女",
    "feminino": "女",
    "laki-laki": "男",
    "perempuan": "女",
    "男性": "男",
    "女性": "女",
    "남": "男",
    "여": "女",
    "남성": "男",
    "여성": "女",
    "非公開": "保密",
    "비공개": "保密",
    "unspecified": "保密",
    "no especificado": "保密",
    "não especificado": "保密",
    "tidak disebutkan": "保密",
}

_GENDER_DISPLAY: dict[str, dict[str, str]] = {
    "zh": {"男": "男", "女": "女", "保密": "保密"},
    "en": {"男": "male", "女": "female", "保密": "unspecified"},
    "ja": {"男": "男性", "女": "女性", "保密": "非公開"},
    "ko": {"男": "남", "女": "여", "保密": "비공개"},
    "pt": {"男": "masculino", "女": "feminino", "保密": "não especificado"},
    "es": {"男": "masculino", "女": "femenino", "保密": "no especificado"},
    "id": {"男": "laki-laki", "女": "perempuan", "保密": "tidak disebutkan"},
}

_USER_CANONICAL = {
    "male": "male",
    "m": "male",
    "man": "male",
    "男": "male",
    "female": "female",
    "f": "female",
    "woman": "female",
    "女": "female",
    "non-binary": "non-binary",
    "nonbinary": "non-binary",
    "非二元": "non-binary",
    "confidential": "confidential",
    "secret": "confidential",
    "other": "confidential",
    "unknown": "confidential",
    "保密": "confidential",
}


def stored_gender(raw: Optional[str], default: str = "女") -> str:
    s = str(raw or "").strip()
    if not s:
        return default
    key = s.lower() if s.isascii() else s
    return _STORED.get(key, default)


def display_gender(raw: Optional[str], lang: Optional[str] = None) -> str:
    """API 展示用性别文案；落库仍用 stored_gender。"""
    stored = stored_gender(raw)
    ui = normalize_ui_language(lang)
    labels = _GENDER_DISPLAY.get(ui) or _GENDER_DISPLAY["en"]
    return labels.get(stored, stored)


def canonical_user_gender(raw: Optional[str], default: str = "") -> str:
    """用户 gender 落库 / 接口返回：male | female | non-binary | confidential。"""
    s = str(raw or "").strip()
    if not s:
        return default
    key = s.lower() if s.isascii() else s
    return _USER_CANONICAL.get(key, default)


def canonical_preferred_gender(raw: Optional[str], default: str = "") -> str:
    """用户 preferred_gender：male | female | non-binary | confidential。"""
    return canonical_user_gender(raw, default=default)
