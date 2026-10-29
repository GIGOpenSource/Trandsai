"""内容语种目录：Accept-Language 通配 → LLM 输出语种 / 随机种子回退。"""
from __future__ import annotations

from core.i18n import normalize_ui_language

# 有专用姓名/城市库 + prompt pack 的语种
PERSONA_SEED_LANGS = frozenset({"zh", "en", "ja", "ko", "pt", "es", "id", "ar", "fa", "ru", "bg", "tr"})

# generate 响应里保持 canonical / 机器可读，不强制 UI 语种
PERSONA_BUSINESS_FIELDS = frozenset({
    "age",
    "gender",              # 男/女
    "sexual_orientation",  # heterosexual / homosexual / …
    "region_key",
    "country",
    "mbti",
})

# LLM 指令用母语名（ISO 639-1 → 英文 + 本地写法）
LANG_NATIVE_NAMES: dict[str, str] = {
    "zh": "Simplified Chinese (简体中文)",
    "en": "English",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "pt": "Portuguese (Português)",
    "es": "Spanish (Español)",
    "id": "Indonesian (Bahasa Indonesia)",
    "ar": "Arabic (العربية)",
    "bg": "Bulgarian (Български)",
    "fa": "Persian (فارسی)",
    "ru": "Russian (Русский)",
    "tr": "Turkish (Türkçe)",
    "hi": "Hindi (हिन्दी)",
    "vi": "Vietnamese (Tiếng Việt)",
    "th": "Thai (ไทย)",
    "de": "German (Deutsch)",
    "fr": "French (Français)",
    "my": "Burmese (မြန်မာ)",
    "km": "Khmer (ខ្មែរ)",
    "it": "Italian (Italiano)",
    "lo": "Lao (ລາວ)",
    "nl": "Dutch (Nederlands)",
    "am": "Amharic (አማርኛ)",
    "pl": "Polish (Polski)",
    "cs": "Czech (Čeština)",
    "da": "Danish (Dansk)",
    "el": "Greek (Ελληνικά)",
    "sv": "Swedish (Svenska)",
    "hu": "Hungarian (Magyar)",
    "fi": "Finnish (Suomi)",
    "ro": "Romanian (Română)",
    "sw": "Swahili (Kiswahili)",
    "nb": "Norwegian (Norsk)",
}

# 12 语专用输出指令（比通用模板更细）
_PERSONA_OUTPUT_INSTRUCTIONS: dict[str, str] = {
    "zh": (
        "【输出语言 — 强制】除姓名与基础信息一致外，personality（性格一句概括）及 life_story、cultural_values、gender_perspective、"
        "appearance、background、speech_style、hobbies、values、fears、love_view、daily_routine、"
        "favorite_things 等所有可朗读字段必须使用自然流畅的简体中文；禁止夹杂英文/日文/韩文等其它语言段落。"
    ),
    "en": (
        "[OUTPUT LANGUAGE — MANDATORY] Except that name may match the given name, every narrative field "
        "(personality one-liner, life_story, cultural_values, gender_perspective, appearance, background, speech_style, hobbies, "
        "values, fears, love_view, daily_routine, favorite_things) MUST be written in clear natural English only. "
        "Do not output Chinese, Japanese, Korean, or other scripts. The city may be non-Western; still write the prose in English."
    ),
    "ja": (
        "【出力言語 — 必須】name 以外の全文（life_story / appearance / background 等）は必ず自然な日本語のみ。"
        "中国語・英語など他言語の段落を混ぜない。都市が日本以外でも本文は日本語。"
    ),
    "ko": (
        "【출력 언어 — 필수】name 외 모든 서술 필드(life_story, appearance, background 등)는 반드시 자연스러운 한국어만 사용. "
        "중국어·영어 등 다른 언어 문단을 섞지 말 것. 도시가 한국이 아니어도 본문은 한국어."
    ),
    "es": (
        "[IDIOMA DE SALIDA — OBLIGATORIO] Salvo el nombre, todo el texto narrativo debe estar solo en español natural. "
        "No mezclar chino, inglés u otros idiomas. Aunque la ciudad no sea hispana, el prosa va en español."
    ),
    "pt": (
        "[IDIOMA DE SAÍDA — OBRIGATÓRIO] Exceto o nome, todo o texto narrativo deve estar apenas em português natural. "
        "Não misturar chinês, inglês ou outros idiomas. Mesmo que a cidade não seja lusófona, o texto vai em português."
    ),
    "id": (
        "[BAHASA OUTPUT — WAJIB] Selain nama, semua field narasi wajib Bahasa Indonesia alami. "
        "Jangan campur Mandarin, Inggris, atau bahasa lain. Kota boleh di luar Indonesia; prosa tetap Bahasa Indonesia."
    ),
    "ar": (
        "[لغة الإخراج — إلزامي] باستثناء الاسم، يجب كتابة كل الحقول السردية "
        "(personality، life_story، cultural_values، gender_perspective، appearance، background، speech_style، hobbies، "
        "values، fears، love_view، daily_routine، favorite_things) بالعربية الفصحى الطبيعية فقط (العربية). "
        "لا تخلط الصينية أو الإنجليزية أو لغات أخرى. حتى لو كانت المدينة غير عربية، يبقى النص بالعربية."
    ),
    "fa": (
        "[زبان خروجی — الزامی] به‌جز نام، تمام فیلدهای روایی "
        "(personality، life_story، cultural_values، gender_perspective، appearance، background، speech_style، hobbies، "
        "values، fears، love_view، daily_routine، favorite_things) باید فقط به فارسی طبیعی نوشته شوند (فارسی). "
        "چینی، انگلیسی یا زبان‌های دیگر را مخلوط نکنید. حتی اگر شهر ایرانی نباشد، نثر به فارسی است."
    ),
    "ru": (
        "[ЯЗЫК ВЫВОДА — ОБЯЗАТЕЛЬНО] Кроме имени, все нарративные поля "
        "(personality, life_story, cultural_values, gender_perspective, appearance, background, speech_style, hobbies, "
        "values, fears, love_view, daily_routine, favorite_things) должны быть только на естественном русском языке (Русский). "
        "Не смешивать китайский, английский или другие языки. Даже если город не российский, текст — на русском."
    ),
    "bg": (
        "[ЕЗИК НА ИЗХОДА — ЗАДЪЛЖИТЕЛЕН] Освен името, всички наративни полета "
        "(personality, life_story, cultural_values, gender_perspective, appearance, background, speech_style, hobbies, "
        "values, fears, love_view, daily_routine, favorite_things) трябва да са само на естествен български (български). "
        "Не смесвай китайски, английски или други езици. Дори градът да не е български, текстът е на български."
    ),
    "tr": (
        "[ÇIKIŞ DİLİ — ZORUNLU] İsim dışında tüm anlatı alanları "
        "(personality, life_story, cultural_values, gender_perspective, appearance, background, speech_style, hobbies, "
        "values, fears, love_view, daily_routine, favorite_things) yalnızca doğal Türkçe (Türkçe) olmalıdır. "
        "Çince, İngilizce veya başka dilleri karıştırma. Şehir Türkçe konuşulan bir yer olmasa bile metin Türkçe kalır."
    ),
}

_PERSONA_HUMAN_MESSAGES: dict[str, str] = {
    "zh": "请根据以上要求生成人设JSON。所有叙事字段必须使用简体中文。",
    "en": "Generate the persona JSON per the rules above. All narrative fields must be in English only.",
    "ja": "上記の要件に従い人設JSONを生成してください。本文はすべて日本語のみ。",
    "ko": "위 요구대로 페르소나 JSON을 생성하세요. 서술 필드는 모두 한국어만 사용하세요.",
    "pt": "Gere o JSON da persona conforme as regras. Todos os campos narrativos devem estar apenas em português.",
    "es": "Genera el JSON de la persona según las reglas. Todos los campos narrativos deben estar solo en español.",
    "id": "Buat JSON persona sesuai aturan di atas. Semua field narasi wajib Bahasa Indonesia saja.",
    "ar": "أنشئ JSON الشخصية وفق القواعد أعلاه. يجب أن تكون جميع الحقول السردية بالعربية فقط (العربية).",
    "fa": "طبق قواعد بالا JSON پرسونا را بساز. همه فیلدهای روایی باید فقط فارسی باشند (فارسی).",
    "ru": "Сгенерируй JSON персоны по правилам выше. Все нарративные поля только на русском (Русский).",
    "bg": "Генерирай JSON на персонажа по правилата. Всички наративни полета само на български (български).",
    "tr": "Yukarıdaki kurallara göre persona JSON'unu üret. Tüm anlatı alanları yalnızca Türkçe (Türkçe) olmalı.",
}


def content_lang(raw: str | None, *, default: str = "en") -> str:
    """Accept-Language / body.lang → companions.language 短码。"""
    return normalize_ui_language(raw) if raw else normalize_ui_language(default)


def lang_native_name(code: str) -> str:
    """LLM 指令中的目标语种名。"""
    c = content_lang(code)
    return LANG_NATIVE_NAMES.get(c, c.upper())


def persona_seed_lang(code: str) -> str:
    """随机姓名/城市库：有专用库用专用库，否则 en（避免 ar 等落到 zh）。"""
    c = content_lang(code)
    if c in PERSONA_SEED_LANGS:
        return c
    return "en"


def persona_output_instruction(code: str) -> str:
    """/companions/generate：强制 LLM 叙事字段使用目标语种。"""
    c = content_lang(code)
    seed_note = (
        " Prompt seed fields (personality trait hints, axis keys) may appear in English; "
        "you must translate and express them naturally in the output language in every narrative field."
    ) if c not in PERSONA_SEED_LANGS else ""
    if c in _PERSONA_OUTPUT_INSTRUCTIONS:
        return _PERSONA_OUTPUT_INSTRUCTIONS[c] + seed_note
    name = lang_native_name(c)
    return (
        f"[OUTPUT LANGUAGE — MANDATORY] Except that field \"name\" may match the given name, every narrative field "
        f"(personality, appearance, background, speech_style, hobbies, values, fears, love_view, daily_routine, "
        f"favorite_things, life_story, cultural_values, gender_perspective) MUST be written in natural, fluent "
        f"{name} only.{seed_note} "
        f"Do not mix Chinese, English, or other languages unless the seed explicitly supports "
        f"a bilingual persona. The city may be anywhere; still write all prose in {name}."
    )


def persona_generate_human_message(code: str) -> str:
    """LLM HumanMessage：再次强调输出语种。"""
    c = content_lang(code)
    if c in _PERSONA_HUMAN_MESSAGES:
        return _PERSONA_HUMAN_MESSAGES[c]
    name = lang_native_name(c)
    return (
        f"Generate the persona JSON per the rules above. "
        f"All narrative fields must be in {name} only."
    )
