"""人设维度轴：在性格标签之外拉开「同 MBTI 不同人」的随机种子。"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

# 轴定义：稳定英文 key + 多语言展示
_AXIS_OPTIONS: Dict[str, List[Dict[str, Any]]] = {
    "career_class": [
        {"key": "student", "labels": {"zh": "学生/尚在求学", "en": "student", "ja": "学生", "ko": "학생", "pt": "estudante", "es": "estudiante", "id": "pelajar", "ar": "طالب", "fa": "دانشجو", "ru": "студент", "bg": "студент", "tr": "öğrenci"}},
        {"key": "entry_white_collar", "labels": {"zh": "职场新人/基层白领", "en": "entry-level white collar", "ja": "若手会社員", "ko": "사회초년생", "pt": "iniciante corporativo", "es": "oficinista junior", "id": "karyawan pemula", "ar": "موظف مبتدئ", "fa": "کارمند تازه‌کار", "ru": "младший офисный", "bg": "младши офис", "tr": "yeni mezun ofis"}},
        {"key": "skilled_trade", "labels": {"zh": "技术工种/手艺人", "en": "skilled trade", "ja": "職人・現場職", "ko": "기술직", "pt": "ofício técnico", "es": "oficio técnico", "id": "pekerja terampil", "ar": "حرفي/مهني", "fa": "فنی‌کار", "ru": "рабочая специальность", "bg": "занаятчия", "tr": "ustalık işi"}},
        {"key": "creative", "labels": {"zh": "创意/内容/设计", "en": "creative / content", "ja": "クリエイティブ", "ko": "크리에이티브", "pt": "criativo", "es": "creativo", "id": "kreatif", "ar": "إبداعي/محتوى", "fa": "خلاق", "ru": "креатив", "bg": "креативен", "tr": "yaratıcı"}},
        {"key": "tech", "labels": {"zh": "科技/工程", "en": "tech / engineering", "ja": "テック・工学", "ko": "테크/엔지니어", "pt": "tech/engenharia", "es": "tech/ingeniería", "id": "teknologi", "ar": "تقنية/هندسة", "fa": "فناوری", "ru": "технологии", "bg": "технологии", "tr": "teknoloji"}},
        {"key": "service_hospitality", "labels": {"zh": "服务/餐饮/零售", "en": "service / hospitality", "ja": "接客・サービス", "ko": "서비스업", "pt": "serviços", "es": "servicios", "id": "jasa/hospitality", "ar": "خدمات/ضيافة", "fa": "خدمات", "ru": "сервис", "bg": "услуги", "tr": "hizmet/ağırlama"}},
        {"key": "public_sector", "labels": {"zh": "体制内/公共部门", "en": "public sector", "ja": "公務員・公共", "ko": "공공/공직", "pt": "setor público", "es": "sector público", "id": "sektor publik", "ar": "قطاع عام", "fa": "بخش عمومی", "ru": "госсектор", "bg": "държавен сектор", "tr": "kamu sektörü"}},
        {"key": "entrepreneur", "labels": {"zh": "创业/自由职业", "en": "entrepreneur / freelance", "ja": "起業・フリー", "ko": "창업/프리랜서", "pt": "empreendedor", "es": "emprendedor", "id": "wirausaha", "ar": "ريادي/حر", "fa": "کارآفرین", "ru": "предприниматель", "bg": "предприемач", "tr": "girişimci"}},
        {"key": "finance_pro", "labels": {"zh": "金融/商务专业岗", "en": "finance / business pro", "ja": "金融・ビジネス", "ko": "금융/비즈니스", "pt": "finanças", "es": "finanzas", "id": "keuangan", "ar": "مالية/أعمال", "fa": "مالی", "ru": "финансы", "bg": "финанси", "tr": "finans"}},
    ],
    "attachment": [
        {"key": "secure", "labels": {"zh": "安全型依恋", "en": "secure attachment", "ja": "安定型愛着", "ko": "안정 애착", "pt": "apego seguro", "es": "apego seguro", "id": "kelekatan aman", "ar": "تعلق آمن", "fa": "دلبستگی ایمن", "ru": "надёжная привязанность", "bg": "сигурна привързаност", "tr": "güvenli bağlanma"}},
        {"key": "anxious", "labels": {"zh": "焦虑型依恋", "en": "anxious attachment", "ja": "不安型愛着", "ko": "불안 애착", "pt": "apego ansioso", "es": "apego ansioso", "id": "kelekatan cemas", "ar": "تعلق قلق", "fa": "دلبستگی اضطرابی", "ru": "тревожная привязанность", "bg": "тревожна привързаност", "tr": "kaygılı bağlanma"}},
        {"key": "avoidant", "labels": {"zh": "回避型依恋", "en": "avoidant attachment", "ja": "回避型愛着", "ko": "회피 애착", "pt": "apego evitante", "es": "apego evitativo", "id": "kelekatan menghindar", "ar": "تعلق تجنبي", "fa": "دلبستگی اجتنابی", "ru": "избегающая привязанность", "bg": "избягваща привързаност", "tr": "kaçınan bağlanma"}},
        {"key": "fearful", "labels": {"zh": "恐惧-矛盾型", "en": "fearful-avoidant", "ja": "恐れ回避型", "ko": "공포-회피", "pt": "medo-evitativo", "es": "temeroso-evitativo", "id": "takut-menghindar", "ar": "تعلق خائف", "fa": "دلبستگی هراسان", "ru": "тревожно-избегающая", "bg": "страх-избягване", "tr": "korkulu-kaçınan"}},
    ],
    "conflict_style": [
        {"key": "direct", "labels": {"zh": "当面直说", "en": "direct confrontation", "ja": "面と向かって話す", "ko": "직접 말하기", "pt": "confronto direto", "es": "confrontación directa", "id": "langsung terang", "ar": "مواجهة مباشرة", "fa": "رودررو", "ru": "прямой разговор", "bg": "директна конфронтация", "tr": "doğrudan yüzleşme"}},
        {"key": "cool_down", "labels": {"zh": "先冷静再谈", "en": "cool down then talk", "ja": "冷めてから話す", "ko": "식힌 뒤 대화", "pt": "esfriar depois falar", "es": "enfriar y hablar", "id": "tenang dulu baru bicara", "ar": "يهدأ ثم يتكلم", "fa": "اول آرام بعد حرف", "ru": "остыть, потом говорить", "bg": "изстини, после говори", "tr": "sakinleşip konuşmak"}},
        {"key": "compromise", "labels": {"zh": "折中求和", "en": "compromising", "ja": "妥協を探る", "ko": "타협 지향", "pt": "compromisso", "es": "compromiso", "id": "kompromi", "ar": "حلول وسط", "fa": "سازش", "ru": "компромисс", "bg": "компромис", "tr": "uzlaşma"}},
        {"key": "competitive", "labels": {"zh": "争赢/好辩", "en": "competitive / debate", "ja": "勝ちにこだわる", "ko": "승부형 논쟁", "pt": "competitivo", "es": "competitivo", "id": "kompetitif", "ar": "جدال/ربح", "fa": "رقابتی", "ru": "спор ради победы", "bg": "състезателен спор", "tr": "tartışmacı"}},
        {"key": "withdraw", "labels": {"zh": "沉默抽离", "en": "withdraw / stonewall", "ja": "黙って距離を取る", "ko": "침묵 거리두기", "pt": "afastar-se", "es": "retirarse", "id": "menarik diri", "ar": "صمت وانسحاب", "fa": "سکوت و فاصله", "ru": "уйти в молчание", "bg": "отдръпване", "tr": "çekilmek"}},
    ],
    "life_pace": [
        {"key": "slow", "labels": {"zh": "慢节奏", "en": "slow pace", "ja": "スローペース", "ko": "느린 템포", "pt": "ritmo lento", "es": "ritmo lento", "id": "tempo lambat", "ar": "إيقاع بطيء", "fa": "آهنگ آهسته", "ru": "медленный ритм", "bg": "бавно темпо", "tr": "yavaş tempo"}},
        {"key": "steady", "labels": {"zh": "稳健规律", "en": "steady routine", "ja": "安定したリズム", "ko": "규칙적 안정", "pt": "rotina estável", "es": "rutina estable", "id": "rutin stabil", "ar": "روتين ثابت", "fa": "روتین پایدار", "ru": "стабильный ритм", "bg": "стабилен ритъм", "tr": "düzenli ritim"}},
        {"key": "fast", "labels": {"zh": "快节奏高压", "en": "fast / high-pressure", "ja": "ハイペース", "ko": "빠른 고압", "pt": "ritmo acelerado", "es": "ritmo rápido", "id": "tempo cepat", "ar": "سريع/ضغط", "fa": "سریع و پرفشار", "ru": "быстрый/давление", "bg": "бърз/напрегнат", "tr": "hızlı tempo"}},
        {"key": "irregular", "labels": {"zh": "作息不规则", "en": "irregular schedule", "ja": "不規則", "ko": "불규칙", "pt": "horário irregular", "es": "horario irregular", "id": "jadwal tidak teratur", "ar": "جدول غير منتظم", "fa": "برنامه نامنظم", "ru": "неровный график", "bg": "нерегулярен график", "tr": "düzensiz program"}},
    ],
    "interest_domain": [
        {"key": "sports", "labels": {"zh": "运动竞技", "en": "sports", "ja": "スポーツ", "ko": "스포츠", "pt": "esportes", "es": "deportes", "id": "olahraga", "ar": "رياضة", "fa": "ورزش", "ru": "спорт", "bg": "спорт", "tr": "spor"}},
        {"key": "arts", "labels": {"zh": "艺术审美", "en": "arts / aesthetics", "ja": "アート", "ko": "예술", "pt": "artes", "es": "artes", "id": "seni", "ar": "فن/جمال", "fa": "هنر", "ru": "искусство", "bg": "изкуство", "tr": "sanat"}},
        {"key": "tech_gadgets", "labels": {"zh": "数码科技", "en": "tech & gadgets", "ja": "ガジェット", "ko": "디지털", "pt": "tech", "es": "gadgets", "id": "gadget", "ar": "تقنية وأجهزة", "fa": "گجت", "ru": "гаджеты", "bg": "джаджи", "tr": "teknoloji/gadget"}},
        {"key": "food", "labels": {"zh": "美食烹饪", "en": "food & cooking", "ja": "食", "ko": "음식", "pt": "comida", "es": "comida", "id": "kuliner", "ar": "طعام وطبخ", "fa": "غذا و آشپزی", "ru": "еда", "bg": "храна", "tr": "yemek"}},
        {"key": "outdoors", "labels": {"zh": "户外自然", "en": "outdoors", "ja": "アウトドア", "ko": "아웃도어", "pt": "ar livre", "es": "aire libre", "id": "outdoor", "ar": "هواء طلق", "fa": "فضای باز", "ru": "природа", "bg": "природа", "tr": "açık hava"}},
        {"key": "gaming", "labels": {"zh": "游戏电竞", "en": "gaming", "ja": "ゲーム", "ko": "게임", "pt": "games", "es": "videojuegos", "id": "game", "ar": "ألعاب", "fa": "گیم", "ru": "игры", "bg": "игри", "tr": "oyun"}},
        {"key": "music", "labels": {"zh": "音乐现场", "en": "music", "ja": "音楽", "ko": "음악", "pt": "música", "es": "música", "id": "musik", "ar": "موسيقى", "fa": "موسیقی", "ru": "музыка", "bg": "музика", "tr": "müzik"}},
        {"key": "reading", "labels": {"zh": "阅读写作", "en": "reading / writing", "ja": "読書・執筆", "ko": "독서/글", "pt": "leitura", "es": "lectura", "id": "membaca", "ar": "قراءة/كتابة", "fa": "خواندن/نوشتن", "ru": "чтение", "bg": "четене", "tr": "okuma/yazma"}},
        {"key": "markets", "labels": {"zh": "财经投资", "en": "markets / investing", "ja": "投資・経済", "ko": "재테크", "pt": "investimentos", "es": "inversiones", "id": "investasi", "ar": "أسواق/استثمار", "fa": "سرمایه", "ru": "инвестиции", "bg": "инвестиции", "tr": "yatırım"}},
        {"key": "travel", "labels": {"zh": "旅行探索", "en": "travel", "ja": "旅行", "ko": "여행", "pt": "viagem", "es": "viajes", "id": "travel", "ar": "سفر", "fa": "سفر", "ru": "путешествия", "bg": "пътешествия", "tr": "seyahat"}},
    ],
}

AXIS_KEYS = tuple(_AXIS_OPTIONS.keys())


def _label(opt: Dict[str, Any], lang: str) -> str:
    labels = opt.get("labels") or {}
    return labels.get(lang) or labels.get("en") or opt["key"]


def list_axis_catalog(lang: str = "zh") -> Dict[str, List[Dict[str, str]]]:
    lang = (lang or "zh").split("-")[0]
    out: Dict[str, List[Dict[str, str]]] = {}
    for axis, opts in _AXIS_OPTIONS.items():
        out[axis] = [{"key": o["key"], "label": _label(o, lang)} for o in opts]
    return out


def sample_persona_axes(lang: str = "zh") -> Dict[str, Any]:
    """随机抽一套维度轴；返回 keys + 当地文案 + 可喂 prompt 的摘要行。"""
    from core.i18n import normalize_ui_language

    lang = normalize_ui_language(lang or "en")
    keys: Dict[str, str] = {}
    labels: Dict[str, str] = {}
    for axis, opts in _AXIS_OPTIONS.items():
        chosen = random.choice(opts)
        keys[axis] = chosen["key"]
        labels[axis] = _label(chosen, lang)
    return {
        "keys": keys,
        "labels": labels,
        "summary_zh": _format_axes_block(labels, "zh"),
        "summary": _format_axes_block(labels, lang),
    }


def resolve_axes_from_payload(data: dict, lang: str = "zh") -> Optional[Dict[str, Any]]:
    """从请求体解析维度轴；缺省返回 None（由调用方决定是否再抽样）。"""
    import json
    from core.i18n import normalize_ui_language

    lang = normalize_ui_language(lang or "en")
    raw_keys = data.get("persona_axes") or data.get("axes") or {}
    if isinstance(raw_keys, str):
        raw = raw_keys.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # 兼容 {"keys": {...}} 或扁平 {...}
                raw_keys = parsed.get("keys") if isinstance(parsed.get("keys"), dict) else parsed
            else:
                return None
        except Exception:
            return None
    keys: Dict[str, str] = {}
    if isinstance(raw_keys, dict):
        for axis in AXIS_KEYS:
            v = raw_keys.get(axis) or data.get(axis)
            if v:
                keys[axis] = str(v).strip()
    # 扁平字段兼容
    for axis in AXIS_KEYS:
        if axis not in keys and data.get(axis):
            keys[axis] = str(data.get(axis)).strip()
    if len(keys) < 3:
        return None
    labels: Dict[str, str] = {}
    for axis, key in keys.items():
        opts = _AXIS_OPTIONS.get(axis) or []
        match = next((o for o in opts if o["key"] == key), None)
        labels[axis] = _label(match, lang) if match else key
    # 补齐缺失轴
    for axis in AXIS_KEYS:
        if axis not in keys:
            opt = random.choice(_AXIS_OPTIONS[axis])
            keys[axis] = opt["key"]
            labels[axis] = _label(opt, lang)
    return {
        "keys": keys,
        "labels": labels,
        "summary": _format_axes_block(labels, lang),
        "summary_zh": _format_axes_block(labels, "zh"),
    }


def _format_axes_block(labels: Dict[str, str], lang: str) -> str:
    titles = {
        "zh": {
            "career_class": "职业阶层",
            "attachment": "依恋类型",
            "conflict_style": "冲突风格",
            "life_pace": "生活节奏",
            "interest_domain": "兴趣域",
        },
        "en": {
            "career_class": "Career class",
            "attachment": "Attachment",
            "conflict_style": "Conflict style",
            "life_pace": "Life pace",
            "interest_domain": "Interest domain",
        },
        "ja": {
            "career_class": "職業階層",
            "attachment": "愛着タイプ",
            "conflict_style": "対立スタイル",
            "life_pace": "生活ペース",
            "interest_domain": "興味分野",
        },
        "ko": {
            "career_class": "직업 계층",
            "attachment": "애착 유형",
            "conflict_style": "갈등 스타일",
            "life_pace": "생활 속도",
            "interest_domain": "관심 분야",
        },
        "pt": {
            "career_class": "Classe profissional",
            "attachment": "Apego",
            "conflict_style": "Estilo de conflito",
            "life_pace": "Ritmo de vida",
            "interest_domain": "Interesse",
        },
        "es": {
            "career_class": "Clase profesional",
            "attachment": "Apego",
            "conflict_style": "Estilo de conflicto",
            "life_pace": "Ritmo de vida",
            "interest_domain": "Interés",
        },
        "id": {
            "career_class": "Kelas karier",
            "attachment": "Gaya kelekatan",
            "conflict_style": "Gaya konflik",
            "life_pace": "Ritme hidup",
            "interest_domain": "Minat",
        },
        "ar": {
            "career_class": "الطبقة المهنية",
            "attachment": "نمط التعلق",
            "conflict_style": "أسلوب النزاع",
            "life_pace": "إيقاع الحياة",
            "interest_domain": "مجال الاهتمام",
        },
        "fa": {
            "career_class": "طبقه شغلی",
            "attachment": "سبک دلبستگی",
            "conflict_style": "سبک تعارض",
            "life_pace": "ریتم زندگی",
            "interest_domain": "حوزه علاقه",
        },
        "ru": {
            "career_class": "Профессиональный класс",
            "attachment": "Тип привязанности",
            "conflict_style": "Стиль конфликта",
            "life_pace": "Темп жизни",
            "interest_domain": "Сфера интересов",
        },
        "bg": {
            "career_class": "Професионален клас",
            "attachment": "Тип привързаност",
            "conflict_style": "Стил на конфликт",
            "life_pace": "Темпо на живот",
            "interest_domain": "Сфера на интерес",
        },
        "tr": {
            "career_class": "Meslek sınıfı",
            "attachment": "Bağlanma stili",
            "conflict_style": "Çatışma stili",
            "life_pace": "Yaşam temposu",
            "interest_domain": "İlgi alanı",
        },
    }
    lang = (lang or "en").split("-")[0]
    tmap = titles.get(lang, titles["en"])
    lines = [f"- {tmap.get(k, k)}: {labels.get(k, '')}" for k in AXIS_KEYS if labels.get(k)]
    return "\n".join(lines)


def localize_axes_for_display(keys: Dict[str, str], lang: str) -> Dict[str, str]:
    """维度轴展示：各轴值为当地语言 label（非英文 key）。"""
    from core.i18n import normalize_ui_language

    lang = normalize_ui_language(lang or "en")
    out: Dict[str, str] = {}
    for axis in AXIS_KEYS:
        raw = str((keys or {}).get(axis) or "").strip()
        if not raw:
            continue
        opts = _AXIS_OPTIONS.get(axis) or []
        match = next((o for o in opts if o["key"] == raw), None)
        if not match:
            match = next(
                (o for o in opts if raw in (o.get("labels") or {}).values()),
                None,
            )
        out[axis] = _label(match, lang) if match else raw
    return out


def normalize_axes_for_storage(raw: Optional[Dict[str, str]]) -> Dict[str, str]:
    """入库前：接受英文 key 或任意语种 label，规范为稳定英文 key。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for axis in AXIS_KEYS:
        token = str(raw.get(axis) or "").strip()
        if not token:
            continue
        opts = _AXIS_OPTIONS.get(axis) or []
        if any(o["key"] == token for o in opts):
            out[axis] = token
            continue
        lower = token.lower()
        matched = None
        for opt in opts:
            if opt["key"].lower() == lower:
                matched = opt["key"]
                break
            labels = opt.get("labels") or {}
            if token in labels.values():
                matched = opt["key"]
                break
            if any(str(v).lower() == lower for v in labels.values()):
                matched = opt["key"]
                break
        out[axis] = matched or token
    return out


def axes_prompt_block(axes: Optional[Dict[str, Any]], lang: str = "zh") -> str:
    if not axes:
        return ""
    summary = axes.get("summary") or axes.get("summary_zh") or ""
    if not summary:
        return ""
    header = {
        "zh": "人设维度种子（必须消化进职业、亲密关系反应、日常节奏与爱好；拉开与同 MBTI 他人的差距，禁止只复述标签）：",
        "en": "Persona dimension seeds (must shape career, intimacy reactions, daily rhythm and hobbies; differentiate from others with the same MBTI):",
        "ar": "بذور أبعاد الشخصية (يجب أن تشكل المهنة، ردود الفعل الحميمة، الإيقاع اليومي والهوايات؛ ميّز عن الآخرين بنفس MBTI):",
        "fa": "بذر ابعاد شخصیت (باید شغل، واکنش صمیمیت، ریتم روزانه و علاقه‌ها را شکل دهد؛ از دیگران با همان MBTI متمایز شو):",
        "ru": "Семена осей персонажа (должны формировать карьеру, реакции близости, ритм дня и хобби; отличаться от других с тем же MBTI):",
        "bg": "Семена на измеренията (трябва да оформят кариера, реакции в близост, дневен ритъм и хобита; отличавай се от други със същото MBTI):",
        "tr": "Kişilik ekseni tohumları (meslek, yakınlık tepkileri, günlük ritim ve hobileri şekillendirmeli; aynı MBTI’daki diğerlerinden ayrış):",
    }.get((lang or "zh").split("-")[0], None)
    if not header:
        header = "Persona dimension seeds (must be reflected in the persona; differentiate same-MBTI characters):"
    return f"\n{header}\n{summary}\n"
