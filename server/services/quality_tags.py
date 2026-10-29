"""对话质量违规 code → 12 语展示文案；改写必答句与失败兜底。"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from services.prompts.resolve import pack_lang

_LIGHT_GREETING = re.compile(
    r"^\s*(?:"
    r"hi+|hii+|hey+|hello+|yo+|sup|howdy|hola|olá|ola+|oi+|halo|hai+|salut|ciao|"
    r"good\s*(?:morning|afternoon|evening)|gm\b|"
    r"ni\s*hao|nihau|"
    r"bonjour|salut|"
    r"merhaba|selam|"
    r"こんにちは|こんばんは|おはよう|やあ|おっす|"
    r"안녕(?:하세요)?|여보세요|"
    r"привет|здравствуй(?:те)?|"
    r"здравей|здрасти|"
    r"مرحبا|السلام عليكم|سلام|"
    r"你好呀?|您好|嗨+|哈喽|在吗|早啊?|早上好|晚上好|午安"
    r")[\s!！.。,~～❤️😊]*\s*$",
    re.I,
)


def is_light_greeting(text: str) -> bool:
    """短问候：不应当必答点，也不该走 must_miss 模板。"""
    t = (text or "").strip()
    if not t or len(t) > 32:
        return False
    return bool(_LIGHT_GREETING.match(t))

# 骨架中文名 → 稳定 code（phase2 仍产出中文名，此处映射）
SKEL_NAME_TO_CODE = {
    "骨架:明日约定": "skeleton_tomorrow",
    "骨架:催睡收尾": "skeleton_sleep",
    "骨架:报梦软着陆": "skeleton_dream",
    "骨架:心疼催睡": "skeleton_pity_sleep",
    "骨架:安全逃题": "skeleton_safe_escape",
    "骨架:连环在干嘛": "skeleton_wyd",
    "骨架:猜谜追问": "skeleton_guess",
    "骨架:嘻嘻开场": "skeleton_hehe",
}

LEAVE_NAME_TO_CODE = {
    "明日约定": "leave_tomorrow",
    "催继续聊": "leave_keep_chatting",
    "新话题钩": "leave_new_hook",
    "连环催睡": "leave_sleep_spam",
}

QUESTION_NAME_TO_CODE = {
    "单条多问句": "question_multi",
    "问句连发": "question_streak",
    "问句占比过高": "question_ratio",
}

# code → {lang: label}
_TAG: Dict[str, Dict[str, str]] = {
    "leitmotif": {
        "zh": "换皮复读",
        "en": "paraphrase repeat",
        "ja": "言い換え反復",
        "ko": "바꿔말하기 반복",
        "pt": "repetição parafraseada",
        "es": "repetición parafraseada",
        "id": "pengulangan parafrasa",
        "ar": "تكرار بإعادة صياغة",
        "fa": "تکرار با بیان دیگر",
        "ru": "повтор перефразом",
        "bg": "повтаряне с перифраза",
        "tr": "anlatım tekrarı",
    },
    "near_dup": {
        "zh": "近复读",
        "en": "near-duplicate",
        "ja": "酷似反復",
        "ko": "유사 반복",
        "pt": "quase duplicata",
        "es": "casi duplicado",
        "id": "hampir duplikat",
        "ar": "تكرار شبه حرفي",
        "fa": "تکرار نزدیک",
        "ru": "почти дословный повтор",
        "bg": "почти буквален повтор",
        "tr": "neredeyse aynı tekrar",
    },
    "theme_repeat": {
        "zh": "忌用主题复读",
        "en": "banned-theme repeat",
        "ja": "禁止テーマの繰り返し",
        "ko": "금기 주제 반복",
        "pt": "tema banido repetido",
        "es": "tema prohibido repetido",
        "id": "tema terlarang diulang",
        "ar": "تكرار موضوع محظور",
        "fa": "تکرار موضوع ممنوع",
        "ru": "повтор запрещённой темы",
        "bg": "повтор на забранена тема",
        "tr": "yasak tema tekrarı",
    },
    "tease_escape": {
        "zh": "黄腔逃题",
        "en": "adult-tease escape",
        "ja": "下ネタからの逃げ",
        "ko": "야한 화제 회피",
        "pt": "fuga de provocação adulta",
        "es": "evasión de provocación adulta",
        "id": "menghindar dari godaan dewasa",
        "ar": "تهرب من مزاح بالغ",
        "fa": "فرار از شوخی بزرگسال",
        "ru": "уход от взрослого намёка",
        "bg": "бягство от възрастен намек",
        "tr": "yetişkin şakadan kaçış",
    },
    "strategy_leak": {
        "zh": "逃题策略自述",
        "en": "narrated topic-escape strategy",
        "ja": "逃げの自己解説",
        "ko": "회피 전략 서술",
        "pt": "narrou estratégia de fuga",
        "es": "narró estrategia de evasión",
        "id": "menceritakan strategi menghindar",
        "ar": "سرد استراتيجية التهرب",
        "fa": "روایت استراتژی فرار",
        "ru": "рассказ о стратегии ухода",
        "bg": "разказ за стратегия за бягство",
        "tr": "kaçış stratejisini anlattı",
    },
    "anti_q_tail": {
        "zh": "反感提问仍问句收尾",
        "en": "ended with question despite anti-ask",
        "ja": "質問嫌いに質問で締めた",
        "ko": "질문 거부인데 질문으로 끝냄",
        "pt": "terminou com pergunta apesar de anti-pergunta",
        "es": "terminó en pregunta pese a anti-pregunta",
        "id": "diakhiri pertanyaan meski anti-tanya",
        "ar": "انتهى بسؤال رغم رفض الأسئلة",
        "fa": "با سؤال تمام شد با وجود نخواستن سؤال",
        "ru": "закончил вопросом вопреки просьбе не спрашивать",
        "bg": "завърши с въпрос въпреки молба да не пита",
        "tr": "sorma demesine rağmen soruyla bitti",
    },
    "action_miss": {
        "zh": "该给行动却问在干嘛",
        "en": "asked wyd instead of giving action",
        "ja": "行動を求められ「何してる」に逃げる",
        "ko": "행동을 물었는데 뭐해로 회피",
        "pt": "pediram ação e respondeu 'o que faz'",
        "es": "pidieron acción y preguntó 'qué haces'",
        "id": "diminta aksi malah tanya lagi ngapain",
        "ar": "طُلب فعل فسأل ماذا تفعل",
        "fa": "درخواست عمل کرد اما پرسید چیکار می‌کنی",
        "ru": "вместо действия спросил «что делаешь»",
        "bg": "вместо действие попита «какво правиш»",
        "tr": "eylem isterken 'ne yapıyorsun' dedi",
    },
    "must_miss": {
        "zh": "未回应必答点",
        "en": "missed must-answer",
        "ja": "必須回答に未対応",
        "ko": "필수 답변 누락",
        "pt": "não cobriu o must-answer",
        "es": "no cubrió el must-answer",
        "id": "wajib jawab terlewat",
        "ar": "فاتت نقطة يجب الرد عليها",
        "fa": "نکتهٔ واجب پاسخ داده نشد",
        "ru": "не закрыл обязательный ответ",
        "bg": "пропусна задължителния отговор",
        "tr": "zorunlu yanıt kaçtı",
    },
    "meta_ack": {
        "zh": "元应答开场",
        "en": "meta-ack opener",
        "ja": "メタ応答オープナー",
        "ko": "메타 응답 오프닝",
        "pt": "abertura meta",
        "es": "apertura meta",
        "id": "pembuka meta",
        "ar": "افتتاحية ميتا",
        "fa": "شروع متا",
        "ru": "мета-открытие",
        "bg": "мета-отваряне",
        "tr": "meta açılış",
    },
    "incomplete_tail": {
        "zh": "话说一半/残句",
        "en": "incomplete / cut-off reply",
        "ja": "途中で切れた返答",
        "ko": "중간에 끊긴 답변",
        "pt": "resposta incompleta",
        "es": "respuesta incompleta",
        "id": "balasan terpotong",
        "ar": "رد غير مكتمل",
        "fa": "پاسخ ناتمام",
        "ru": "обрыв ответа",
        "bg": "недовършен отговор",
        "tr": "yarım kalmış yanıt",
    },
    "user_echo": {
        "zh": "复读用户原文",
        "en": "echoed user fragment",
        "ja": "ユーザー文のエコー",
        "ko": "사용자 문장 메아리",
        "pt": "ecoou o usuário",
        "es": "eco del usuario",
        "id": "mengulang kata pengguna",
        "ar": "ترديد كلام المستخدم",
        "fa": "تکرار حرف کاربر",
        "ru": "эхо фразы пользователя",
        "bg": "ехо на потребителя",
        "tr": "kullanıcıyı tekrarlama",
    },
    "moderation_tone": {
        "zh": "审核/训诫/关机口吻",
        "en": "moderation / end-chat tone",
        "ja": "注意・説教・終話口調",
        "ko": "훈계/검열/종료 말투",
        "pt": "tom de advertência / encerrar",
        "es": "tono de amonestación / cerrar chat",
        "id": "nada menegur / akhiri chat",
        "ar": "نبرة وعظ/إنهاء الحوار",
        "fa": "لحن نصیحت‌گر/قطع گفتگو",
        "ru": "тон нотации / конец диалога",
        "bg": "тон на мъмрене / край",
        "tr": "uyarı/vaaz / sohbeti bitirme",
    },
    "stage_direction": {
        "zh": "舞台动作括号",
        "en": "stage-direction parentheses",
        "ja": "ト書き括弧",
        "ko": "무대지시 괄호",
        "pt": "indicações de cena",
        "es": "acotaciones teatrales",
        "id": "arah panggung",
        "ar": "إرشادات مشهد",
        "fa": "دستور صحنه",
        "ru": "ремарки в скобках",
        "bg": "сценни ремарки",
        "tr": "sahne yönergesi",
    },
    "refuse_only": {
        "zh": "只拒不接",
        "en": "refuse-only / off-topic refuse",
        "ja": "拒否のみで未応答",
        "ko": "거절만 하고 미응답",
        "pt": "só recusa sem responder",
        "es": "solo rechazo sin atender",
        "id": "hanya menolak",
        "ar": "رفض دون تفاعل",
        "fa": "فقط رد بدون پاسخ",
        "ru": "только отказ",
        "bg": "само отказ",
        "tr": "sadece red",
    },
    "process_leak": {
        "zh": "过程/约束泄漏",
        "en": "process / constraint leak",
        "ja": "過程・制約の漏出",
        "ko": "과정/제약 유출",
        "pt": "vazamento de processo",
        "es": "fuga de proceso",
        "id": "bocor proses",
        "ar": "تسرب عملية",
        "fa": "نشت فرآیند",
        "ru": "утечка процесса",
        "bg": "изтичане на процес",
        "tr": "süreç sızıntısı",
    },
    "multi_ask_miss": {
        "zh": "多问漏答",
        "en": "missed parts of multi-ask",
        "ja": "複数質問の取りこぼし",
        "ko": "여러 질문 누락",
        "pt": "perguntas múltiplas incompletas",
        "es": "preguntas múltiples incompletas",
        "id": "banyak pertanyaan terlewat",
        "ar": "أسئلة متعددة ناقصة",
        "fa": "چندسؤال ناقص",
        "ru": "не закрыл несколько вопросов",
        "bg": "пропуснати въпроси",
        "tr": "çoklu soru eksik",
    },
    "near_dup_fallback": {
        "zh": "近几轮同款/安全逃题",
        "en": "same as recent / safe-topic escape",
        "ja": "直近と同型/安全話題への逃げ",
        "ko": "최근과 同型/안전 화제 회피",
        "pt": "igual ao recente / fuga segura",
        "es": "igual a lo reciente / fuga segura",
        "id": "sama seperti baru-baru / kabur aman",
        "ar": "مثل الردود الأخيرة / هروب آمن",
        "fa": "شبیه اخیر / فرار امن",
        "ru": "как в недавних / безопасный уход",
        "bg": "като наскоро / безопасно бягство",
        "tr": "yakın tekrarı / güvenli kaçış",
    },
    "opener_spam": {
        "zh": "开场口头禅复用",
        "en": "opener spam",
        "ja": "決まり文句の開き直し",
        "ko": "오프닝 습관 반복",
        "pt": "abertura repetida",
        "es": "apertura repetida",
        "id": "pembuka berulang",
        "ar": "افتتاحية مكررة",
        "fa": "شروع تکراری",
        "ru": "повтор открытия",
        "bg": "повторен увод",
        "tr": "tekrarlayan açılış",
    },
    "question_multi": {
        "zh": "单条多问句",
        "en": "multiple questions in one reply",
        "ja": "一文に複数の疑問",
        "ko": "한 메시지에 질문 여러 개",
        "pt": "várias perguntas numa mensagem",
        "es": "varias preguntas en un mensaje",
        "id": "banyak pertanyaan dalam satu pesan",
        "ar": "عدة أسئلة في رسالة واحدة",
        "fa": "چند سؤال در یک پیام",
        "ru": "несколько вопросов в одном сообщении",
        "bg": "няколко въпроса в едно съобщение",
        "tr": "bir mesajda birden fazla soru",
    },
    "question_streak": {
        "zh": "问句连发",
        "en": "question streak",
        "ja": "疑問文の連発",
        "ko": "질문 연발",
        "pt": "sequência de perguntas",
        "es": "racha de preguntas",
        "id": "rentetan pertanyaan",
        "ar": "سلسلة أسئلة",
        "fa": "رشته سؤال",
        "ru": "серия вопросов",
        "bg": "поредица от въпроси",
        "tr": "soru zinciri",
    },
    "question_ratio": {
        "zh": "问句占比过高",
        "en": "too many question endings",
        "ja": "疑問終わりが多すぎ",
        "ko": "질문 마무리 비율 과다",
        "pt": "demasiadas perguntas",
        "es": "demasiadas preguntas",
        "id": "terlalu banyak pertanyaan",
        "ar": "أسئلة أكثر من اللازم",
        "fa": "سؤال بیش از حد",
        "ru": "слишком много вопросов",
        "bg": "твърде много въпроси",
        "tr": "çok fazla soru",
    },
    "skeleton_tomorrow": {
        "zh": "骨架:明日约定",
        "en": "skeleton:tomorrow-promise",
        "ja": "骨格:明日の約束",
        "ko": "골격:내일 약속",
        "pt": "esqueleto:promessa-amanhã",
        "es": "esqueleto:promesa-mañana",
        "id": "kerangka:janji-besok",
        "ar": "هيكل:وعد الغد",
        "fa": "قالب:قول فردا",
        "ru": "скелет:обещание-завтра",
        "bg": "скелет:обещание-утре",
        "tr": "iskelet:yarın-sözü",
    },
    "skeleton_sleep": {
        "zh": "骨架:催睡收尾",
        "en": "skeleton:sleep-nudge",
        "ja": "骨格:寝かしつけ",
        "ko": "골격:재우기 마무리",
        "pt": "esqueleto:empurrar-sono",
        "es": "esqueleto:empujar-sueño",
        "id": "kerangka:dorong-tidur",
        "ar": "هيكل:حث على النوم",
        "fa": "قالب:هل دادن به خواب",
        "ru": "скелет:уложить-спать",
        "bg": "скелет:към-сън",
        "tr": "iskelet:uyutma",
    },
    "skeleton_dream": {
        "zh": "骨架:报梦软着陆",
        "en": "skeleton:dream-softland",
        "ja": "骨格:夢の着地",
        "ko": "골격:꿈 연착륙",
        "pt": "esqueleto:sonho-suave",
        "es": "esqueleto:sueño-suave",
        "id": "kerangka:mimpi-lembut",
        "ar": "هيكل:هبوط حلم",
        "fa": "قالب:فرود نرم خواب",
        "ru": "скелет:сон-мягкая-посадка",
        "bg": "скелет:сън-меко",
        "tr": "iskelet:rüya-yumuşak",
    },
    "skeleton_pity_sleep": {
        "zh": "骨架:心疼催睡",
        "en": "skeleton:pity-then-sleep",
        "ja": "骨格:心配して寝かせる",
        "ko": "골격:안쓰러워 재우기",
        "pt": "esqueleto:dó-e-sono",
        "es": "esqueleto:pena-y-sueño",
        "id": "kerangka:kasihan-lalu-tidur",
        "ar": "هيكل:شفقة ثم نوم",
        "fa": "قالب:دل‌سوزی و خواب",
        "ru": "скелет:жалость-и-сон",
        "bg": "скелет:жалост-и-сън",
        "tr": "iskelet:acıma-uyu",
    },
    "skeleton_safe_escape": {
        "zh": "骨架:安全逃题",
        "en": "skeleton:safe-escape",
        "ja": "骨格:安全話題への逃げ",
        "ko": "골격:안전 화제 회피",
        "pt": "esqueleto:fuga-segura",
        "es": "esqueleto:fuga-segura",
        "id": "kerangka:kabur-aman",
        "ar": "هيكل:هروب آمن",
        "fa": "قالب:فرار امن",
        "ru": "скелет:безопасный-уход",
        "bg": "скелет:безопасно-бягство",
        "tr": "iskelet:güvenli-kaçış",
    },
    "skeleton_wyd": {
        "zh": "骨架:连环在干嘛",
        "en": "skeleton:wyd-loop",
        "ja": "骨格:何してる連発",
        "ko": "골격:뭐해 연발",
        "pt": "esqueleto:o-que-faz-loop",
        "es": "esqueleto:qué-haces-loop",
        "id": "kerangka:ngapain-loop",
        "ar": "هيكل:ماذا تفعل متكرر",
        "fa": "قالب:چیکار می‌کنی تکراری",
        "ru": "скелет:что-делаешь-цикл",
        "bg": "скелет:какво-правиш-цикъл",
        "tr": "iskelet:ne-yapıyorsun-döngü",
    },
    "skeleton_guess": {
        "zh": "骨架:猜谜追问",
        "en": "skeleton:guess-chase",
        "ja": "骨格:当てて追問",
        "ko": "골격:맞춰봐 추궁",
        "pt": "esqueleto:adivinhe",
        "es": "esqueleto:adivina",
        "id": "kerangka:tebak",
        "ar": "هيكل:خمّن",
        "fa": "قالب:حدس بزن",
        "ru": "скелет:угадай",
        "bg": "скелет:познай",
        "tr": "iskelet:bil-bakalım",
    },
    "skeleton_hehe": {
        "zh": "骨架:嘻嘻开场",
        "en": "skeleton:hehe-opener",
        "ja": "骨格:へへ開き",
        "ko": "골격:히히 오프닝",
        "pt": "esqueleto:hehe-abertura",
        "es": "esqueleto:jeje-apertura",
        "id": "kerangka:hehe-pembuka",
        "ar": "هيكل:افتتاحية ضحك",
        "fa": "قالب:شروع هی‌هی",
        "ru": "скелет:хехе-открытие",
        "bg": "скелет:хехе-увод",
        "tr": "iskelet:hehe-açılış",
    },
    "leave_tomorrow": {
        "zh": "离开后明日约定",
        "en": "leave+tomorrow promise",
        "ja": "終了後に明日約束",
        "ko": "종료 후 내일 약속",
        "pt": "saída+promessa amanhã",
        "es": "salida+promesa mañana",
        "id": "pergi+janji besok",
        "ar": "مغادرة+وعد غد",
        "fa": "خروج+قول فردا",
        "ru": "уход+обещание завтра",
        "bg": "тръгване+обещание утре",
        "tr": "ayrılık+yarın sözü",
    },
    "leave_keep_chatting": {
        "zh": "离开后催继续聊",
        "en": "leave+keep chatting",
        "ja": "終了後に続けさせようとする",
        "ko": "종료 후 계속 유도",
        "pt": "saída+continuar papo",
        "es": "salida+seguir charla",
        "id": "pergi+ajak lanjut",
        "ar": "مغادرة+حث على المواصلة",
        "fa": "خروج+تشویق به ادامه",
        "ru": "уход+удержать в чате",
        "bg": "тръгване+задържане",
        "tr": "ayrılık+konuşturmaya devam",
    },
    "leave_new_hook": {
        "zh": "离开后新话题钩",
        "en": "leave+new hook",
        "ja": "終了後に新フック",
        "ko": "종료 후 새 훅",
        "pt": "saída+gancho novo",
        "es": "salida+gancho nuevo",
        "id": "pergi+kail baru",
        "ar": "مغادرة+خطاف جديد",
        "fa": "خروج+قلاب تازه",
        "ru": "уход+новый крючок",
        "bg": "тръгване+нова кука",
        "tr": "ayrılık+yeni kanca",
    },
    "leave_sleep_spam": {
        "zh": "离开后连环催睡",
        "en": "leave+sleep spam",
        "ja": "終了後に寝かしつけ連打",
        "ko": "종료 후 재우기 연타",
        "pt": "saída+sono em loop",
        "es": "salida+sueño en bucle",
        "id": "pergi+dorong tidur beruntun",
        "ar": "مغادرة+حث نوم متكرر",
        "fa": "خروج+هل دادن مکرر به خواب",
        "ru": "уход+укладывание-спам",
        "bg": "тръгване+спам за сън",
        "tr": "ayrılık+uyutma spam",
    },
}

_REWRITE_MUST = {
    "zh": "\n【必答优先】先写清必答点（{must}）；答完前禁止新钩子/催睡/安全逃题。",
    "en": "\n[MUST-ANSWER FIRST] Cover these points first ({must}); no new hooks/sleep-nudge/safe escape before answering.",
    "ja": "\n【必須回答優先】先に必須点を書く（{must}）。答える前に新フック/寝かしつけ/安全逃げ禁止。",
    "ko": "\n【필수 답변 우선】먼저 필수 포인트（{must}）. 답하기 전 새 훅/재우기/안전 회피 금지.",
    "pt": "\n[MUST-ANSWER PRIMEIRO] Cubra primeiro ({must}); sem ganchos/sono/fuga segura antes de responder.",
    "es": "\n[MUST-ANSWER PRIMERO] Cubre primero ({must}); sin ganchos/sueño/fuga segura antes de responder.",
    "id": "\n[WAJIB JAWAB DULU] Tutup dulu ({must}); tanpa kait/tidur/kabur aman sebelum menjawab.",
    "ar": "\n[الرد الإلزامي أولاً] غطِ أولاً ({must})؛ بلا خطاطيف/نوم/هروب آمن قبل الإجابة.",
    "fa": "\n[اول پاسخ واجب] اول این‌ها را بگو ({must})؛ قبل از جواب قلاب/خواب/فرار امن ممنوع.",
    "ru": "\n[СНАЧАЛА ОБЯЗАТЕЛЬНЫЙ ОТВЕТ] Сначала закрой ({must}); без крючков/сна/безопасного ухода до ответа.",
    "bg": "\n[ПЪРВО ЗАДЪЛЖИТЕЛНИЯТ ОТГОВОР] Първо покрий ({must}); без куки/сън/безопасно бягство преди отговор.",
    "tr": "\n[ÖNCE ZORUNLU YANIT] Önce şunları karşıla ({must}); cevaplamadan kanca/uyku/güvenli kaçış yok.",
}


def _slot_ok_for_lang(text: str, lang: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    n_zh = len(re.findall(r"[\u4e00-\u9fff]", s))
    n_ja = len(re.findall(r"[\u3040-\u30ff]", s))
    n_ko = len(re.findall(r"[\uac00-\ud7af]", s))
    n_ar = len(re.findall(r"[\u0600-\u06FF]", s))
    n_cyr = len(re.findall(r"[\u0400-\u04FF]", s))
    n_lat = len(re.findall(r"[A-Za-z]", s))
    if lang == "zh":
        return n_zh >= 1
    if lang == "ja":
        return n_ja >= 1
    if lang == "ko":
        return n_ko >= 1
    if lang in ("ar", "fa"):
        return n_ar >= 1
    if lang in ("ru", "bg"):
        return n_cyr >= 1
    return n_lat >= 2 and (n_zh + n_ja + n_ko + n_ar + n_cyr) < 2


def _strip_must_meta_prefix(text: str) -> str:
    s = (text or "").strip()
    return re.sub(
        r"^(回应|回复|回答|必须回应|must[\s-]*answer|respond(?:\s+to)?)\s*[:：]?\s*",
        "",
        s,
        flags=re.I,
    ).strip()


def _visible_must_slot(must: str, language: str, user_input: str) -> str:
    """用户气泡里只放与回复语同脚本的原文，禁止塞中文内部备注。"""
    lang = pack_lang(language)
    for cand in (_strip_must_meta_prefix(must), (user_input or "").strip()):
        if _slot_ok_for_lang(cand, lang):
            return cand[:80]
    return ""


def tag_label(code: str, language: str = "zh") -> str:
    lang = pack_lang(language)
    row = _TAG.get(code) or {}
    return row.get(lang) or row.get("en") or code


def localize_ban_summary(codes: List[str], language: str = "zh", *, must_answer: str = "") -> str:
    lang = pack_lang(language)
    if not codes:
        return tag_label("near_dup_fallback", lang)
    parts: List[str] = []
    for c in codes:
        if c == "must_miss" and (must_answer or "").strip():
            parts.append(f"{tag_label('must_miss', lang)}:{must_answer.strip()[:60]}")
        else:
            parts.append(tag_label(c, lang))
    joiner = "、" if lang == "zh" else "; "
    return joiner.join(parts)


def rewrite_must_bit(language: str, must_answer: str, user_input: str = "") -> str:
    lang = pack_lang(language)
    ma = _visible_must_slot(must_answer, lang, user_input)
    if not ma:
        return ""
    tpl = _REWRITE_MUST.get(lang) or _REWRITE_MUST["en"]
    return tpl.format(must=ma[:120])


_TURN_POLICY_HINT = {
    "ANSWER_FIRST": {
        "zh": "\n[TURN_POLICY=ANSWER_FIRST] 本轮先答必答点，答完前禁止新钩子。",
        "en": "\n[TURN_POLICY=ANSWER_FIRST] Cover must-answer first; no new hooks before that.",
    },
    "LEAVE_CLOSE": {
        "zh": "\n[TURN_POLICY=LEAVE_CLOSE] 单气泡收束离开；禁止明日约定/催聊/新钩。",
        "en": "\n[TURN_POLICY=LEAVE_CLOSE] Single-bubble close; no tomorrow-promise/keep-chatting/new hooks.",
    },
    "HOLD_EMOTION": {
        "zh": "\n[TURN_POLICY=HOLD_EMOTION] 直接回应情绪/尺度所指，禁止安全逃题；禁止「我听到了」式空应答。",
        "en": "\n[TURN_POLICY=HOLD_EMOTION] Answer the emotional/tease referent; no safe-topic escape; no empty 'I heard you' ack.",
    },
    "CLARIFY_ONCE": {
        "zh": "\n[TURN_POLICY=CLARIFY_ONCE] 最多一句澄清，禁止连环追问。",
        "en": "\n[TURN_POLICY=CLARIFY_ONCE] At most one clarification; no question streak.",
    },
    "LIGHT_FLIRT": {
        "zh": "\n[TURN_POLICY=LIGHT_FLIRT] 可轻撩，但仍先回应本轮内容。",
        "en": "\n[TURN_POLICY=LIGHT_FLIRT] Light flirt ok, but still address this turn first.",
    },
    "ADVANCE_THREAD": {
        "zh": "\n[TURN_POLICY=ADVANCE_THREAD] 推进已有话题债，勿另起安全题。",
        "en": "\n[TURN_POLICY=ADVANCE_THREAD] Advance an open thread; no safe-topic detour.",
    },
}


def turn_policy_block(policy: str, language: str = "zh") -> str:
    p = (policy or "").strip().upper()
    if not p:
        return ""
    lang = pack_lang(language)
    row = _TURN_POLICY_HINT.get(p) or {}
    return row.get(lang) or row.get("en") or f"\n[TURN_POLICY={p}]"
