# 后端多语言字典 —— 供 core/config.py 及各服务模块导入使用
from __future__ import annotations

import json
import re
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Optional

# locale/*.json 词表语种（UI 文案）；缺词表时 _() 仍回退英文
_LOCALE_UI_LANGS = frozenset({"zh", "en", "ja", "ko", "pt", "es", "id"})
# 兼容旧名
_SUPPORTED_UI_LANGS = _LOCALE_UI_LANGS

# 常见误传 / ISO 639-2/3 → 库内 companions.language 短码
_LANG_ALIASES = {
    "jp": "ja",
    "jpn": "ja",
    "kr": "ko",
    "kor": "ko",
    "cn": "zh",
    "zho": "zh",
    "cmn": "zh",
    "no": "nb",
    "nn": "nb",
    "fas": "fa",
    "per": "fa",
    "arb": "ar",
    "ind": "id",
    "spa": "es",
    "por": "pt",
    "rus": "ru",
    "tur": "tr",
    "bul": "bg",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "vie": "vi",
    "tha": "th",
    "hin": "hi",
    "mya": "my",
    "bur": "my",
    "khm": "km",
    "lao": "lo",
    "ita": "it",
    "nld": "nl",
    "dut": "nl",
    "pol": "pl",
    "ces": "cs",
    "cze": "cs",
    "dan": "da",
    "ell": "el",
    "gre": "el",
    "swe": "sv",
    "hun": "hu",
    "fin": "fi",
    "ron": "ro",
    "rum": "ro",
    "swa": "sw",
    "amh": "am",
}


def normalize_ui_language(lang: Optional[str]) -> str:
    """将浏览器 / 客户端传入值规范为 companions.language 可用的短码。

    - zh-CN / zh-Hans → zh；ar-SA → ar；en-US → en
    - 前端/库内常见语种直接保留（通配 ISO 639-1 两字母）
    - 仅空值或无法识别的长串回退 en（文案词表仍可按需回退英文）
    """
    if not lang:
        return "en"
    raw = str(lang).strip().replace("_", "-")
    lower = raw.lower()
    if lower.startswith("zh"):
        return "zh"
    base = raw.split("-")[0].lower().strip()
    if not base:
        return "en"
    base = _LANG_ALIASES.get(base, base)
    # ISO 639-1：与库内 language、前端 Accept-Language 通配匹配
    if len(base) == 2 and base.isalpha():
        return base
    return "en"


# 离开意图关键词（8 语言）
_LEAVE_INTENT_KEYWORDS = [
    # 中文
    "走了", "去忙", "晚安", "拜拜", "再见", "睡了", "累了", "改天", "先这样",
    "回头聊", "下了", "撤了", "不聊了", "有事", "要忙", "先走", "回聊",
    # 英文
    "gotta go", "gtg", "ttyl", "goodnight", "bye", "later", "sleep", "tired",
    "gotta run", "brb", "afk", "leaving", "need to go", "busy now",
    # 日语
    "行くね", "おやすみ", "バイバイ", "またね", "疲れた", "後で", "忙しい",
    # 韩语
    "가야 해", "잘 자", "바이바이", "나중에", "피곤해", "바빠",
    # 葡萄牙语（巴西）
    "tchau", "adeus", "até logo", "vou indo", "preciso ir", "boa noite",
    "estou cansado", "depois", "mais tarde", "até mais", "falou",
    "tenho que ir", "tô ocupado", "vou sair", "depois a gente conversa",
    # 西班牙语
    "adiós", "chao", "hasta luego", "me voy", "tengo que irme", "buenas noches",
    "estoy cansado", "después", "luego", "hasta mañana", "nos vemos",
    "debo irme", "estoy ocupado", "salgo", "hablamos después",
    # 印尼语
    "dadah", "selamat tinggal", "sampai jumpa", "aku pergi", "aku harus pergi",
    "selamat malam", "aku lelah", "nanti", "lain kali", "sampai besok",
    "sampai nanti", "aku sibuk", "aku pergi dulu",     "kita ngobrol nanti",
    # Arabic
    "مع السلامة", "تصبح على خير", "باي", "أنا رايح", "لازم أمشي", "نعسان", "مشغول",
    "أروح", "بكرة", "سلام",
    # Persian
    "خداحافظ", "شب بخیر", "باید برم", "خسته‌ام", "فردا", "بای", "می‌رم",
    # Russian
    "пока", "спокойной ночи", "мне пора", "устал", "занят", "до завтра", "давай",
    # Bulgarian
    "чао", "лека нощ", "трябва да вървя", "уморен съм", "зает съм", "довиждане",
    # Turkish
    "görüşürüz", "iyi geceler", "gitmem lazım", "yorgunum", "meşgulüm", "hoşça kal",
]

# 短时间重复发送相同用户文时的弱提示（极短/明显连点外尽量少打扰）
_DUPLICATE_USER_MESSAGE = {
    "zh": "和刚才那条一样哦，我按一条听啦",
    "en": "Same as your last one — I’ve got it as one message.",
    "ja": "さっきと同じ文面みたい。一本として聞きました",
    "ko": "아까랑 똑같은 말이네. 한 덩어리로 알았어",
    "pt": "Igual à mensagem anterior — contei como uma.",
    "es": "Es igual a la de antes: lo tengo en cuenta como uno.",
    "id": "Sama seperti tadi—aku anggep satu aja.",
    "ar": "نفس الرسالة السابقة — اعتبرتها واحدة.",
    "fa": "همان پیام قبلی است — یکی حساب کردم.",
    "ru": "То же, что в прошлый раз — считаю одним сообщением.",
    "bg": "Същото като предишното — броя го като едно.",
    "tr": "Az öncekiyle aynı — tek mesaj saydım.",
}

# 等模型生成时的弱反馈（不写入对话记忆，仅作「还在」感）
_LLM_WAIT_FILLER = {
    "zh": ["嗯…", "等下，我在想怎么说", "稍等哦", "让我组织一下~"],
    "en": ["Mmm…", "Hold on, thinking", "One sec~", "Let me put that into words…"],
    "ja": ["うーん…", "ちょっと待ってね", "今、言葉にするね", "少し考え中…"],
    "ko": ["응…", "잠깐만, 어떻게 말할지 생각 중", "곧~", "정리해볼게…"],
    "pt": ["Uhm…", "Só um segundo", "Tô pensando em como dizer…", "Jájá~"],
    "es": ["Mmm…", "Dame un segundo", "Pensando cómo decirlo…", "Un momento~"],
    "id": ["Hmm…", "Sebentar ya, mikirin jawabnya", "Tunggu~", "Aku urusin kata-katanya dulu~"],
    "ar": ["مم…", "لحظة، أفكر كيف أقول", "ثانية~", "خليني أصيغها…"],
    "fa": ["هم…", "یک لحظه فکر کنم", "صبر کن~", "بگذار جور کنم…"],
    "ru": ["Мм…", "Секунду, думаю", "Сейчас~", "Подберу слова…"],
    "bg": ["Мм…", "Секунда, мисля", "Веднага~", "Да подбера думите…"],
    "tr": ["Hmm…", "Bir saniye, nasıl diyeceğimi düşünüyorum", "Bekle~", "Cümleyi kurayım…"],
}

# LLM / agent 超时
# 发送期间队列合并：积压多条时合并为一轮，由模型整体理解后统一回复
_QUEUE_COALESCED_MESSAGE = {
    "zh": "已合并你连续发送的多条消息，对方会结合上下文一并理解后回复",
    "en": "Multiple messages were merged; your companion will read them together and reply once.",
    "ja": "連続メッセージをまとめました。文脈を踏まえて一度に返信します",
    "ko": "연속 메시지를 합쳤어요. 맥락을 보고 한 번에 답할게요",
    "pt": "Várias mensagens foram reunidas; vou ler tudo junto e responder de uma vez.",
    "es": "Se juntaron varios mensajes; leeré todo junto y responderé de una vez.",
    "id": "Beberapa pesan digabung; akan dibaca sekaligus lalu dibalas satu kali.",
    "ar": "دُمجت عدة رسائل؛ سيقرأها معاً ويرد مرة واحدة.",
    "fa": "چند پیام یکی شد؛ با هم خوانده می‌شود و یک بار جواب می‌آید.",
    "ru": "Несколько сообщений объединены; прочту вместе и отвечу один раз.",
    "bg": "Няколко съобщения са обединени; ще ги прочета заедно и ще отговоря веднъж.",
    "tr": "Birkaç mesaj birleştirildi; hepsini okuyup bir kez yanıtlayacağım.",
}

# 多条并入一轮时，附加在发给 LLM 的用户输入前的说明（勿改「编号」行格式过多，便于模型解析）
# 禁止示范「你刚说的××」等元应答开场
_MULTI_MESSAGE_AGENT_PREFIX = {
    "zh": "【对方刚刚连续发送了以下多条消息。抓住最后一条里最关键的意图，结合全文与上文语境，用通顺口语整体回复一次。直接答内容，禁止用「你刚说的/我听到了」开头复述；不要对每条逐条机械应答、也不要像读清单】\n\n",
    "en": "[They just sent several messages in a row below. Grab the last/strongest intent, read everything with context, and reply **once** in natural speech. Answer the content directly—do NOT open with “you said / I heard you”. No bullet-by-bullet or list-reading tone.]\n\n",
    "ja": "【相手が続けて複数送っています。最後の要点に直接答え、全文と文脈を踏まえ自然に**一度**返してください。「さっき言った／聞こえた」で始めない。行ごとに機械的に答えたり箇条書きのように聞かないでください】\n\n",
    "ko": "【상대가 연속으로 여러 메시지를 보냈습니다. 가장 최근/핵심 의도에 바로 답하고, 전체·맥락을 보고 **한 번**에 자연스럽게 답하세요. “방금 말한/들었어”로 시작하지 마세요. 줄마다 기계적으로 답하거나 목록 읽듯 말하지 마세요】\n\n",
    "pt": "[Várias mensagens seguidas. Responda direto ao ponto mais forte/último, leia tudo com contexto e responda **uma vez**—sem abrir com “você disse / te ouvi”; sem estilo de lista.]\n\n",
    "es": "[Varios mensajes seguidos. Responde directo a la última idea fuerte, con contexto, **una vez**—sin abrir con “dijiste / te oí”; sin listado.]\n\n",
    "id": "[Beberapa pesan berurutan. Jawab langsung poin terakhir/terkuat, baca semua dengan konteks, balas **sekali**—jangan buka dengan “kamu bilang / aku dengar”; jangan gaya daftar.]\n\n",
    "ar": "[أرسلوا عدة رسائل متتالية. أجب مباشرة عن أقوى/آخر نقطة، اقرأ الكل مع السياق واردّ **مرة واحدة**—بلا افتتاح بـ قلتَ/سمعتك.]\n\n",
    "fa": "[چند پیام پشت‌سرهم. مستقیم به آخرین/قوی‌ترین نکته جواب بده، همه را با بافت بخوان و **یک‌بار** جواب بده—با «گفتی/شنیدم» شروع نکن.]\n\n",
    "ru": "[Несколько сообщений подряд. Ответь по сути на последнюю/главную мысль, прочти всё с контекстом и ответь **один раз**—не начинай с «ты сказал / слышу».]\n\n",
    "bg": "[Няколко последователни съобщения. Отговори директно на последното/най-силното, прочети всичко с контекст и отговори **веднъж**—без „каза / чувам те“.]\n\n",
    "tr": "[Ardışık birkaç mesaj. Son/en güçlü noktaya doğrudan cevap ver, hepsini bağlamla oku ve **bir kez** yanıtla—“dedin / duydum” ile açma.]\n\n",
}

_AGENT_TIMEOUT_MESSAGE = {
    "zh": "生成回复超时，请稍后再试",
    "en": "Reply timed out. Please try again.",
    "ja": "応答がタイムアウトしました。もう一度お試しください",
    "ko": "응답 시간이 초과됐어요. 다시 시도해 주세요",
    "pt": "Tempo esgotado. Tente novamente.",
    "es": "Tiempo de espera agotado. Inténtalo de nuevo.",
    "id": "Waktu habis. Coba lagi.",
    "ar": "انتهت مهلة الرد. حاول مرة أخرى.",
    "fa": "زمان پاسخ تمام شد. دوباره تلاش کن.",
    "ru": "Время ответа вышло. Попробуйте ещё раз.",
    "bg": "Времето за отговор изтече. Опитай пак.",
    "tr": "Yanıt zaman aşımına uğradı. Tekrar dene.",
}

_CONNECT_MESSAGES = {
    "zh": lambda name: f"已连接到 {name}，开始聊天吧~",
    "en": lambda name: f"Connected to {name}. Let's chat~",
    "ja": lambda name: f"{name}に接続しました。お話ししましょう~",
    "ko": lambda name: f"{name}에 연결됐어. 이야기하자~",
    "pt": lambda name: f"Conectado a {name}. Vamos conversar~",
    "es": lambda name: f"Conectado a {name}. ¡Hablemos~!",
    "id": lambda name: f"Terhubung dengan {name}. Mari ngobrol~",
    "ar": lambda name: f"تم الاتصال بـ {name}. هيا نتحدث~",
    "fa": lambda name: f"به {name} وصل شدی. حرف بزنیم~",
    "ru": lambda name: f"Подключено к {name}. Давай общаться~",
    "bg": lambda name: f"Свързан с {name}. Да говорим~",
    "tr": lambda name: f"{name} ile bağlandın. Konuşalım~",
}

# WebSocket 连接阶段错误（与客户端 lang 查询参数对齐）
_WS_AUTH_FAILED = {
    "zh": "认证失败，请重新登录",
    "en": "Authentication failed. Please sign in again.",
    "ja": "認証に失敗しました。再度ログインしてください",
    "ko": "인증에 실패했습니다. 다시 로그인해 주세요",
    "pt": "Falha na autenticação. Entre novamente.",
    "es": "Error de autenticación. Vuelve a iniciar sesión.",
    "id": "Autentikasi gagal. Silakan masuk lagi.",
    "ar": "فشل التوثيق. سجّل الدخول مجدداً.",
    "fa": "احراز هویت شکست خورد. دوباره وارد شو.",
    "ru": "Ошибка входа. Войдите снова.",
    "bg": "Неуспешна идентификация. Влез отново.",
    "tr": "Kimlik doğrulama başarısız. Yeniden giriş yap.",
}

_WS_COMPANION_NOT_FOUND = {
    "zh": "智能体不存在，请先创建",
    "en": "This companion doesn’t exist yet. Please create one first.",
    "ja": "このコンパニオンはまだありません。先に作成してください",
    "ko": "해당 AI가 없습니다. 먼저 만들어 주세요",
    "pt": "Este companheiro não existe. Crie um primeiro.",
    "es": "Este compañero no existe. Créalo primero.",
    "id": "Teman AI ini belum ada. Buat dulu ya.",
    "ar": "هذا الرفيق غير موجود بعد. أنشئه أولاً.",
    "fa": "این همراه هنوز وجود ندارد. اول بساز.",
    "ru": "Этого компаньона ещё нет. Сначала создайте.",
    "bg": "Този компаньон още го няма. Създай го първо.",
    "tr": "Bu arkadaş henüz yok. Önce oluştur.",
}

# 软删除后禁止聊天（其他人仍可见资料）
_WS_COMPANION_SOFT_DELETED = {
    "zh": "该智能体已经被删除",
    "en": "This companion has been deleted.",
    "ja": "このコンパニオンは削除されました",
    "ko": "이 AI는 삭제되었습니다",
    "pt": "Este companheiro foi excluído.",
    "es": "Este compañero ha sido eliminado.",
    "id": "Teman AI ini sudah dihapus.",
    "ar": "تم حذف هذا الرفيق.",
    "fa": "این همراه حذف شده است.",
    "ru": "Этот компаньон удалён.",
    "bg": "Този компаньон е изтрит.",
    "tr": "Bu arkadaş silindi.",
}

_WS_ACCESS_DENIED = {
    "zh": "无权访问该智能体",
    "en": "You don’t have access to this companion.",
    "ja": "このコンパニオンにアクセスできません",
    "ko": "이 AI에 접근할 수 없습니다",
    "pt": "Sem permissão para acessar este companheiro.",
    "es": "No tienes acceso a este compañero.",
    "id": "Kamu tidak punya akses ke teman AI ini.",
    "ar": "ليست لديك صلاحية الوصول إلى هذا الرفيق.",
    "fa": "به این همراه دسترسی نداری.",
    "ru": "Нет доступа к этому компаньону.",
    "bg": "Нямаш достъп до този компаньон.",
    "tr": "Bu arkadaşa erişim yetkin yok.",
}

_WS_CHAT_UNEXPECTED_ERROR = {
    "zh": "聊天服务出现异常，请稍后重试",
    "en": "Something went wrong. Please try again later.",
    "ja": "チャットで問題が発生しました。しばらくしてからお試しください",
    "ko": "채팅에 문제가 생겼어요. 잠시 후 다시 시도해 주세요",
    "pt": "Algo deu errado no chat. Tente de novo mais tarde.",
    "es": "Hubo un problema en el chat. Inténtalo más tarde.",
    "id": "Terjadi masalah di obrolan. Coba lagi nanti.",
    "ar": "حدث خلل في الدردشة. حاول لاحقاً.",
    "fa": "در گفتگو مشکلی پیش آمد. بعداً دوباره تلاش کن.",
    "ru": "Сбой в чате. Попробуйте позже.",
    "bg": "Проблем в чата. Опитай по-късно.",
    "tr": "Sohbette bir sorun oldu. Daha sonra dene.",
}

# 消息列表：最后一条为朋友圈详情卡片时的预览文案
_LAST_MESSAGE_FROM_MOMENT = {
    "zh": "来自朋友圈",
    "en": "From Moments",
    "ja": "モーメントから",
    "ko": "모멘트에서",
    "pt": "Dos Moments",
    "es": "De Moments",
    "id": "Dari Moments",
}

_AUTH_REQUIRED = {
    "zh": "请先登录",
    "en": "Please sign in",
    "ja": "ログインしてください",
    "ko": "로그인해 주세요",
    "pt": "Faça login primeiro",
    "es": "Inicia sesión primero",
    "id": "Silakan masuk dulu",
}

_AUTH_EXPIRED = {
    "zh": "登录已失效",
    "en": "Session expired. Please sign in again.",
    "ja": "ログインの有効期限が切れました",
    "ko": "로그인이 만료됐어요. 다시 로그인해 주세요",
    "pt": "Sessão expirada. Entre novamente.",
    "es": "Sesión caducada. Vuelve a iniciar sesión.",
    "id": "Sesi berakhir. Masuk lagi ya.",
}


def http_ui_lang(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    """HTTP 错误文案用语种：显式 lang > Accept-Language > en。"""
    if lang:
        return normalize_ui_language(lang)
    from services.prompts.resolve import resolve_lang

    return resolve_lang(accept_language=accept_language, default="en")


def _pick_http_msg(table: dict, accept_language: Optional[str] = None, lang: Optional[str] = None) -> str:
    key = http_ui_lang(accept_language=accept_language, lang=lang)
    return table.get(key) or table["en"]


def auth_required_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_AUTH_REQUIRED, accept_language=accept_language, lang=lang)


def auth_expired_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_AUTH_EXPIRED, accept_language=accept_language, lang=lang)


def companion_not_found_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(
        _WS_COMPANION_NOT_FOUND, accept_language=accept_language, lang=lang
    )


def companion_forbidden_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(
        _WS_ACCESS_DENIED, accept_language=accept_language, lang=lang
    )


def companion_deleted_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(
        _WS_COMPANION_SOFT_DELETED, accept_language=accept_language, lang=lang
    )


_VIP_REQUIRED = {
    "zh": "请先开通VIP",
    "en": "VIP is required",
    "ja": "VIPの登録が必要です",
    "ko": "VIP가 필요해요",
    "pt": "É necessário ser VIP",
    "es": "Se requiere VIP",
    "id": "Perlu VIP dulu",
}

_CREDITS_INSUFFICIENT = {
    "zh": "积分不足，请充值",
    "en": "Not enough credits. Please recharge.",
    "ja": "ポイントが不足しています。チャージしてください",
    "ko": "포인트가 부족해요. 충전해 주세요",
    "pt": "Créditos insuficientes. Recarregue.",
    "es": "Créditos insuficientes. Recarga.",
    "id": "Kredit kurang. Isi ulang dulu.",
}

_USER_NOT_FOUND = {
    "zh": "用户不存在",
    "en": "User not found",
    "ja": "ユーザーが見つかりません",
    "ko": "사용자를 찾을 수 없어요",
    "pt": "Usuário não encontrado",
    "es": "Usuario no encontrado",
    "id": "Pengguna tidak ditemukan",
}

_EMPTY_CONTENT = {
    "zh": "内容不能为空",
    "en": "Content cannot be empty",
    "ja": "内容を入力してください",
    "ko": "내용을 입력해 주세요",
    "pt": "O conteúdo não pode ficar vazio",
    "es": "El contenido no puede estar vacío",
    "id": "Isi tidak boleh kosong",
}

_ACCOUNT_DELETED = {
    "zh": "账号已注销",
    "en": "Account deleted",
    "ja": "アカウントを削除しました",
    "ko": "계정이 삭제됐어요",
    "pt": "Conta excluída",
    "es": "Cuenta eliminada",
    "id": "Akun sudah dihapus",
}

_ACCOUNT_PENDING_DELETION = {
    "zh": "账户将在{days}天后注销，可取消注销后继续使用",
    "en": "Your account will be permanently closed in {days} day(s). You can cancel deletion to keep using it.",
    "ja": "アカウントは{days}日後に完全削除されます。削除を取り消すと引き続き利用できます",
    "ko": "계정이 {days}일 후 영구 삭제됩니다. 삭제를 취소하면 계속 사용할 수 있어요",
    "pt": "Sua conta será encerrada permanentemente em {days} dia(s). Você pode cancelar a exclusão.",
    "es": "Tu cuenta se cerrará permanentemente en {days} día(s). Puedes cancelar la eliminación.",
    "id": "Akun akan ditutup permanen dalam {days} hari. Kamu bisa batalkan penghapusan.",
}

_ACCOUNT_CLOSED = {
    "zh": "账号已销户，请重新注册",
    "en": "This account has been permanently closed. Please register again.",
    "ja": "アカウントは完全に閉鎖されました。新規登録してください",
    "ko": "계정이 영구 삭제됐어요. 다시 가입해 주세요",
    "pt": "Esta conta foi encerrada permanentemente. Cadastre-se novamente.",
    "es": "Esta cuenta se cerró permanentemente. Regístrate de nuevo.",
    "id": "Akun ini sudah ditutup permanen. Silakan daftar lagi.",
}

_ACCOUNT_RESTORED = {
    "zh": "已取消注销，账号已恢复",
    "en": "Account deletion canceled. Your account is active again.",
    "ja": "削除を取り消しました。アカウントが復旧しました",
    "ko": "삭제가 취소됐고 계정이 복구됐어요",
    "pt": "Exclusão cancelada. Sua conta está ativa novamente.",
    "es": "Eliminación cancelada. Tu cuenta está activa de nuevo.",
    "id": "Penghapusan dibatalkan. Akun kamu aktif lagi.",
}

_NO_UPDATE_FIELDS = {
    "zh": "没有可更新的字段",
    "en": "No fields to update",
    "ja": "更新する項目がありません",
    "ko": "수정할 항목이 없어요",
    "pt": "Nenhum campo para atualizar",
    "es": "No hay campos para actualizar",
    "id": "Tidak ada field yang diubah",
}

_FORBIDDEN = {
    "zh": "无权执行该操作",
    "en": "You don’t have permission to do that.",
    "ja": "この操作を行う権限がありません",
    "ko": "이 작업을 할 권한이 없어요",
    "pt": "Sem permissão para esta ação.",
    "es": "No tienes permiso para esta acción.",
    "id": "Kamu tidak punya izin untuk ini.",
}

_POST_NOT_FOUND = {
    "zh": "帖子不存在",
    "en": "Post not found",
    "ja": "投稿が見つかりません",
    "ko": "글을 찾을 수 없어요",
    "pt": "Post não encontrado",
    "es": "Publicación no encontrada",
    "id": "Postingan tidak ditemukan",
}

_TICKET_ISSUE_FAILED = {
    "zh": "票据签发失败",
    "en": "Failed to issue ticket",
    "ja": "チケットの発行に失敗しました",
    "ko": "티켓 발급에 실패했어요",
    "pt": "Falha ao emitir o ticket",
    "es": "No se pudo emitir el ticket",
    "id": "Gagal menerbitkan tiket",
}

_DELETE_FAILED = {
    "zh": "删除失败",
    "en": "Delete failed",
    "ja": "削除に失敗しました",
    "ko": "삭제하지 못했어요",
    "pt": "Falha ao excluir",
    "es": "No se pudo eliminar",
    "id": "Gagal menghapus",
}

_PROFILE_UPDATE_FAILED = {
    "zh": "用户信息更新失败",
    "en": "Failed to update profile",
    "ja": "プロフィールを更新できませんでした",
    "ko": "프로필을 수정하지 못했어요",
    "pt": "Falha ao atualizar o perfil",
    "es": "No se pudo actualizar el perfil",
    "id": "Gagal memperbarui profil",
}

_AGENT_LIMIT = {
    "zh": "已达智能体上限({n}个)，请升级VIP",
    "en": "Companion limit reached ({n}). Upgrade VIP.",
    "ja": "コンパニオン上限（{n}）に達しました。VIPにアップグレードしてください",
    "ko": "AI 한도({n}명)에 도달했어요. VIP로 업그레이드해 주세요",
    "pt": "Limite de companheiros ({n}) atingido. Faça upgrade do VIP.",
    "es": "Límite de compañeros ({n}) alcanzado. Mejora a VIP.",
    "id": "Batas teman AI ({n}) tercapai. Upgrade VIP.",
}


def vip_required_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_VIP_REQUIRED, accept_language=accept_language, lang=lang)


def credits_insufficient_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(
        _CREDITS_INSUFFICIENT, accept_language=accept_language, lang=lang
    )


def user_not_found_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_USER_NOT_FOUND, accept_language=accept_language, lang=lang)


def empty_content_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_EMPTY_CONTENT, accept_language=accept_language, lang=lang)


def account_deleted_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_ACCOUNT_DELETED, accept_language=accept_language, lang=lang)


def account_pending_deletion_message(
    accept_language: Optional[str] = None,
    lang: Optional[str] = None,
    *,
    days_left: int = 7,
) -> str:
    tmpl = _pick_http_msg(_ACCOUNT_PENDING_DELETION, accept_language=accept_language, lang=lang)
    try:
        return tmpl.format(days=max(1, int(days_left)))
    except Exception:
        return tmpl


def account_closed_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_ACCOUNT_CLOSED, accept_language=accept_language, lang=lang)


def account_restored_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_ACCOUNT_RESTORED, accept_language=accept_language, lang=lang)


def no_update_fields_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_NO_UPDATE_FIELDS, accept_language=accept_language, lang=lang)


def profile_update_failed_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(
        _PROFILE_UPDATE_FAILED, accept_language=accept_language, lang=lang
    )


def forbidden_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_FORBIDDEN, accept_language=accept_language, lang=lang)


def post_not_found_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_POST_NOT_FOUND, accept_language=accept_language, lang=lang)


def ticket_issue_failed_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(
        _TICKET_ISSUE_FAILED, accept_language=accept_language, lang=lang
    )


def delete_failed_message(
    accept_language: Optional[str] = None, lang: Optional[str] = None
) -> str:
    return _pick_http_msg(_DELETE_FAILED, accept_language=accept_language, lang=lang)


def agent_limit_message(
    max_agents: int,
    accept_language: Optional[str] = None,
    lang: Optional[str] = None,
) -> str:
    key = http_ui_lang(accept_language=accept_language, lang=lang)
    tmpl = _AGENT_LIMIT.get(key) or _AGENT_LIMIT["en"]
    return tmpl.format(n=int(max_agents or 0))


_VALIDATION = {
    "nickname_too_long": {
        "zh": "昵称最长32个字符",
        "en": "Nickname must be at most 32 characters",
        "ja": "ニックネームは32文字以内です",
        "ko": "닉네임은 32자 이하여야 해요",
        "pt": "O apelido deve ter no máximo 32 caracteres",
        "es": "El apodo debe tener como máximo 32 caracteres",
        "id": "Nama panggilan maksimal 32 karakter",
    },
    "bad_birth_date": {
        "zh": "出生日期格式错误，请使用 YYYY-MM-DD",
        "en": "Invalid birth date. Use YYYY-MM-DD",
        "ja": "生年月日は YYYY-MM-DD で入力してください",
        "ko": "생년월일은 YYYY-MM-DD 형식이어야 해요",
        "pt": "Data de nascimento inválida. Use AAAA-MM-DD",
        "es": "Fecha de nacimiento no válida. Usa AAAA-MM-DD",
        "id": "Tanggal lahir salah. Pakai YYYY-MM-DD",
    },
    "username_too_short": {
        "zh": "用户名至少3个字符",
        "en": "Username must be at least 3 characters",
        "ja": "ユーザー名は3文字以上です",
        "ko": "아이디는 3자 이상이어야 해요",
        "pt": "O usuário deve ter pelo menos 3 caracteres",
        "es": "El usuario debe tener al menos 3 caracteres",
        "id": "Username minimal 3 karakter",
    },
    "email_too_long": {
        "zh": "邮箱过长",
        "en": "Email is too long",
        "ja": "メールアドレスが長すぎます",
        "ko": "이메일이 너무 길어요",
        "pt": "E-mail longo demais",
        "es": "El correo es demasiado largo",
        "id": "Email terlalu panjang",
    },
    "password_too_short": {
        "zh": "密码至少6个字符",
        "en": "Password must be at least 6 characters",
        "ja": "パスワードは6文字以上です",
        "ko": "비밀번호는 6자 이상이어야 해요",
        "pt": "A senha deve ter pelo menos 6 caracteres",
        "es": "La contraseña debe tener al menos 6 caracteres",
        "id": "Password minimal 6 karakter",
    },
    "username_exists": {
        "zh": "用户名已存在",
        "en": "Username already exists",
        "ja": "このユーザー名は既に使われています",
        "ko": "이미 사용 중인 아이디예요",
        "pt": "Este usuário já existe",
        "es": "Este usuario ya existe",
        "id": "Username sudah dipakai",
    },
    "name_city_personality_required": {
        "zh": "姓名、城市和性格为必填项",
        "en": "Name, city and personality are required",
        "ja": "名前・都市・性格は必須です",
        "ko": "이름, 도시, 성격은 필수예요",
        "pt": "Nome, cidade e personalidade são obrigatórios",
        "es": "Nombre, ciudad y personalidad son obligatorios",
        "id": "Nama, kota, dan kepribadian wajib diisi",
    },
    "image_type_not_allowed": {
        "zh": "仅支持 jpeg/png/gif/webp/heic 图片",
        "en": "Only jpeg/png/gif/webp/heic images are supported",
        "ja": "jpeg/png/gif/webp/heic のみ対応です",
        "ko": "jpeg/png/gif/webp/heic만 지원해요",
        "pt": "Apenas imagens jpeg/png/gif/webp/heic",
        "es": "Solo imágenes jpeg/png/gif/webp/heic",
        "id": "Hanya jpeg/png/gif/webp/heic",
    },
    "image_empty": {
        "zh": "图片文件为空",
        "en": "Image file is empty",
        "ja": "画像ファイルが空です",
        "ko": "이미지 파일이 비어 있어요",
        "pt": "O arquivo de imagem está vazio",
        "es": "El archivo de imagen está vacío",
        "id": "File gambar kosong",
    },
    "image_too_large": {
        "zh": "图片大小不能超过 40MB",
        "en": "Image must be 40MB or smaller",
        "ja": "画像は 40MB 以下にしてください",
        "ko": "이미지는 40MB 이하여야 해요",
        "pt": "A imagem deve ter no máximo 40MB",
        "es": "La imagen debe ser de 40MB o menos",
        "id": "Gambar maksimal 40MB",
    },
    "image_heic_decode_failed": {
        "zh": "HEIC 原图解码失败，请关闭「原图」或另存为 JPG 后再传",
        "en": "Failed to decode HEIC original. Turn off Original or save as JPG and retry",
        "ja": "HEIC の原図を解読できません。原図をオフにするか JPG で保存して再送してください",
        "ko": "HEIC 원본을 열 수 없어요. 원본 전송을 끄거나 JPG로 저장한 뒤 다시 올려 주세요",
        "pt": "Falha ao decodificar HEIC original. Desative Original ou salve como JPG",
        "es": "No se pudo decodificar el HEIC original. Desactiva Original o guarda como JPG",
        "id": "Gagal decode HEIC original. Matikan Original atau simpan sebagai JPG",
    },
    "image_decode_failed": {
        "zh": "图片解码失败，请换一张或改为 JPG/PNG 后再传",
        "en": "Failed to decode the image. Try another file or save as JPG/PNG",
        "ja": "画像を解読できません。別のファイルか JPG/PNG で再送してください",
        "ko": "이미지를 열 수 없어요. 다른 파일이거나 JPG/PNG로 다시 올려 주세요",
        "pt": "Falha ao decodificar a imagem. Tente outro arquivo ou JPG/PNG",
        "es": "No se pudo decodificar la imagen. Prueba otro archivo o JPG/PNG",
        "id": "Gagal decode gambar. Coba file lain atau JPG/PNG",
    },
    "image_compress_failed": {
        "zh": "图片压缩失败，请换一张较小的图",
        "en": "Failed to compress the image. Try a smaller photo",
        "ja": "画像の圧縮に失敗しました。より小さい写真を試してください",
        "ko": "이미지 압축에 실패했어요. 더 작은 사진을 올려 주세요",
        "pt": "Falha ao compactar a imagem. Tente uma foto menor",
        "es": "Error al comprimir la imagen. Prueba una foto más pequeña",
        "id": "Gagal kompres gambar. Coba foto lebih kecil",
    },
    "image_compress_still_too_large": {
        "zh": "压缩后仍超过 10MB，请换一张较小的图",
        "en": "Still over 10MB after compression. Try a smaller photo",
        "ja": "圧縮後も 10MB を超えています。より小さい写真を試してください",
        "ko": "압축 후에도 10MB를 넘어요. 더 작은 사진을 올려 주세요",
        "pt": "Ainda acima de 10MB após compactar. Tente uma foto menor",
        "es": "Sigue superando 10MB tras comprimir. Prueba una foto más pequeña",
        "id": "Masih di atas 10MB setelah kompres. Coba foto lebih kecil",
    },
    "image_storage_not_configured": {
        "zh": "图片存储未配置（COS/GCS），请联系管理员",
        "en": "Image storage is not configured (COS/GCS). Contact support",
        "ja": "画像ストレージが未設定です（COS/GCS）。管理者に連絡してください",
        "ko": "이미지 저장소가 설정되지 않았어요 (COS/GCS). 관리자에게 문의하세요",
        "pt": "Armazenamento de imagem não configurado (COS/GCS)",
        "es": "Almacenamiento de imagen no configurado (COS/GCS)",
        "id": "Penyimpanan gambar belum dikonfigurasi (COS/GCS)",
    },
    "image_storage_upload_failed": {
        "zh": "图片上传到存储失败，请稍后重试",
        "en": "Failed to upload the image to storage. Please retry",
        "ja": "ストレージへのアップロードに失敗しました。後でもう一度お試しください",
        "ko": "저장소 업로드에 실패했어요. 잠시 후 다시 시도해 주세요",
        "pt": "Falha ao enviar a imagem para o armazenamento. Tente de novo",
        "es": "Error al subir la imagen al almacenamiento. Reintenta",
        "id": "Gagal unggah ke storage. Coba lagi",
    },
    "image_upload_failed": {
        "zh": "图片上传失败：COS未配置或上传失败",
        "en": "Image upload failed",
        "ja": "画像のアップロードに失敗しました",
        "ko": "이미지 업로드에 실패했어요",
        "pt": "Falha no upload da imagem",
        "es": "Error al subir la imagen",
        "id": "Gagal unggah gambar",
    },
    "moment_not_found": {
        "zh": "朋友圈不存在",
        "en": "Moment not found",
        "ja": "モーメントが見つかりません",
        "ko": "모멘트를 찾을 수 없어요",
        "pt": "Momento não encontrado",
        "es": "Momento no encontrado",
        "id": "Momen tidak ditemukan",
    },
    "moment_owner_only": {
        "zh": "仅创建者可重新生成配图",
        "en": "Only the creator can regenerate the image",
        "ja": "作成者のみ配図を再生成できます",
        "ko": "만든 사람만 이미지를 다시 생성할 수 있어요",
        "pt": "Só o criador pode regenerar a imagem",
        "es": "Solo el creador puede regenerar la imagen",
        "id": "Hanya pembuat yang bisa regenerate gambar",
    },
    "missing_id_token": {
        "zh": "缺少 id_token",
        "en": "id_token is required",
        "ja": "id_token が必要です",
        "ko": "id_token이 필요해요",
        "pt": "id_token é obrigatório",
        "es": "Se requiere id_token",
        "id": "id_token wajib",
    },
    "google_token_invalid": {
        "zh": "Google token 无效",
        "en": "Google token is invalid",
        "ja": "Google トークンが無効です",
        "ko": "Google 토큰이 유효하지 않아요",
        "pt": "Token do Google inválido",
        "es": "Token de Google no válido",
        "id": "Token Google tidak valid",
    },
    "username_empty": {
        "zh": "用户名不能为空",
        "en": "Username is required",
        "ja": "ユーザー名を入力してください",
        "ko": "아이디를 입력해 주세요",
        "pt": "O usuário é obrigatório",
        "es": "El usuario es obligatorio",
        "id": "Username wajib",
    },
    "password_required": {
        "zh": "请输入密码",
        "en": "Password is required",
        "ja": "パスワードを入力してください",
        "ko": "비밀번호를 입력해 주세요",
        "pt": "A senha é obrigatória",
        "es": "La contraseña es obligatoria",
        "id": "Password wajib",
    },
    "username_or_password_wrong": {
        "zh": "用户名或密码错误",
        "en": "Incorrect username or password",
        "ja": "ユーザー名またはパスワードが正しくありません",
        "ko": "아이디 또는 비밀번호가 올바르지 않아요",
        "pt": "Usuário ou senha incorretos",
        "es": "Usuario o contraseña incorrectos",
        "id": "Username atau password salah",
    },
    "username_taken": {
        "zh": "该用户已存在",
        "en": "This account already exists",
        "ja": "このアカウントは既に存在します",
        "ko": "이미 있는 계정이에요",
        "pt": "Esta conta já existe",
        "es": "Esta cuenta ya existe",
        "id": "Akun ini sudah ada",
    },
    "username_available": {
        "zh": "该用户名可用",
        "en": "Username is available",
        "ja": "このユーザー名は使えます",
        "ko": "사용할 수 있는 아이디예요",
        "pt": "Este usuário está disponível",
        "es": "Este usuario está disponible",
        "id": "Username tersedia",
    },
    "missing_id_token_or_code": {
        "zh": "缺少 id_token 或 code",
        "en": "id_token or code is required",
        "ja": "id_token または code が必要です",
        "ko": "id_token 또는 code가 필요해요",
        "pt": "id_token ou code é obrigatório",
        "es": "Se requiere id_token o code",
        "id": "id_token atau code wajib",
    },
    "apple_code_exchange_failed": {
        "zh": "Apple code 换取 token 失败",
        "en": "Could not exchange Apple code for a token",
        "ja": "Apple code のトークン交換に失敗しました",
        "ko": "Apple code로 토큰을 받지 못했어요",
        "pt": "Falha ao trocar o code da Apple",
        "es": "No se pudo canjear el code de Apple",
        "id": "Gagal tukar code Apple",
    },
    "apple_login_failed": {
        "zh": "Apple 登录验证失败",
        "en": "Apple sign-in verification failed",
        "ja": "Apple ログインの検証に失敗しました",
        "ko": "Apple 로그인 검증에 실패했어요",
        "pt": "Falha na verificação do login Apple",
        "es": "Falló la verificación de Apple",
        "id": "Verifikasi login Apple gagal",
    },
    "login_retry_later": {
        "zh": "登录失败，请稍后重试",
        "en": "Sign-in failed. Please try again later",
        "ja": "ログインに失敗しました。しばらくして再試行してください",
        "ko": "로그인에 실패했어요. 잠시 후 다시 시도해 주세요",
        "pt": "Falha no login. Tente novamente mais tarde",
        "es": "Error al iniciar sesión. Inténtalo más tarde",
        "id": "Login gagal. Coba lagi nanti",
    },
    "oauth_missing_id": {
        "zh": "缺少第三方用户标识",
        "en": "Missing third-party user id",
        "ja": "第三者ユーザーIDがありません",
        "ko": "서드파티 사용자 ID가 없어요",
        "pt": "Falta o id do usuário de terceiros",
        "es": "Falta el id de usuario de terceros",
        "id": "ID pengguna pihak ketiga hilang",
    },
    "oauth_username_exhausted": {
        "zh": "无法分配用户名，请重试",
        "en": "Could not assign a username. Please retry",
        "ja": "ユーザー名を割り当てできません。再試行してください",
        "ko": "아이디를 만들 수 없어요. 다시 시도해 주세요",
        "pt": "Não foi possível atribuir um usuário. Tente de novo",
        "es": "No se pudo asignar un usuario. Reintenta",
        "id": "Tidak bisa buat username. Coba lagi",
    },
    "guest_device_id_required": {
        "zh": "请提供有效的设备唯一标识 device_id（至少 8 位）",
        "en": "A valid device_id is required (min 8 characters)",
        "ja": "有効な device_id が必要です（8文字以上）",
        "ko": "유효한 device_id가 필요해요(최소 8자)",
        "pt": "Informe um device_id válido (mín. 8 caracteres)",
        "es": "Se requiere un device_id válido (mín. 8 caracteres)",
        "id": "device_id valid wajib (min 8 karakter)",
    },
    "guest_device_id_invalid": {
        "zh": "device_id 格式无效",
        "en": "Invalid device_id format",
        "ja": "device_id の形式が無効です",
        "ko": "device_id 형식이 올바르지 않아요",
        "pt": "Formato de device_id inválido",
        "es": "Formato de device_id no válido",
        "id": "Format device_id tidak valid",
    },
    "guest_cannot_pay": {
        "zh": "临时登录无法支付，请先使用 Google 或 Apple 登录绑定账号",
        "en": "Guest login cannot purchase. Please sign in with Google or Apple first",
        "ja": "ゲストログインでは購入できません。先に Google / Apple でログインしてください",
        "ko": "게스트 로그인은 결제할 수 없어요. 먼저 Google 또는 Apple로 로그인해 주세요",
        "pt": "Conta convidada não pode pagar. Entre com Google ou Apple",
        "es": "La cuenta invitada no puede pagar. Inicia sesión con Google o Apple",
        "id": "Login tamu tidak bisa bayar. Login Google/Apple dulu",
    },
    "missing_action": {
        "zh": "缺少 action 参数",
        "en": "action is required",
        "ja": "action が必要です",
        "ko": "action이 필요해요",
        "pt": "action é obrigatório",
        "es": "Se requiere action",
        "id": "action wajib",
    },
    "missing_receipt_data": {
        "zh": "缺少 receipt_data",
        "en": "receipt_data is required",
        "ja": "receipt_data が必要です",
        "ko": "receipt_data가 필요해요",
        "pt": "receipt_data é obrigatório",
        "es": "Se requiere receipt_data",
        "id": "receipt_data wajib",
    },
    "product_type_invalid": {
        "zh": "product_type 必须为 vip 或 recharge",
        "en": "product_type must be vip or recharge",
        "ja": "product_type は vip または recharge です",
        "ko": "product_type은 vip 또는 recharge여야 해요",
        "pt": "product_type deve ser vip ou recharge",
        "es": "product_type debe ser vip o recharge",
        "id": "product_type harus vip atau recharge",
    },
    "vip_missing_plan": {
        "zh": "VIP 购买缺少 plan_type",
        "en": "VIP purchase requires plan_type",
        "ja": "VIP 購入には plan_type が必要です",
        "ko": "VIP 구매에는 plan_type이 필요해요",
        "pt": "Compra VIP exige plan_type",
        "es": "La compra VIP requiere plan_type",
        "id": "Pembelian VIP butuh plan_type",
    },
    "recharge_missing_package": {
        "zh": "充值缺少 package_key",
        "en": "Recharge requires package_key",
        "ja": "チャージには package_key が必要です",
        "ko": "충전에는 package_key가 필요해요",
        "pt": "Recarga exige package_key",
        "es": "La recarga requiere package_key",
        "id": "Isi ulang butuh package_key",
    },
    "apple_receipt_invalid": {
        "zh": "Apple 收据验证失败",
        "en": "Apple receipt verification failed",
        "ja": "Apple レシートの検証に失敗しました",
        "ko": "Apple 영수증 검증에 실패했어요",
        "pt": "Falha na verificação do recibo Apple",
        "es": "Falló la verificación del recibo de Apple",
        "id": "Verifikasi struk Apple gagal",
    },
    "product_id_mismatch": {
        "zh": "产品ID不匹配，购买内容与请求不符",
        "en": "Product ID does not match this purchase",
        "ja": "プロダクトIDが購入内容と一致しません",
        "ko": "제품 ID가 구매 내용과 맞지 않아요",
        "pt": "O ID do produto não confere com a compra",
        "es": "El ID de producto no coincide con la compra",
        "id": "Product ID tidak cocok dengan pembelian",
    },
    "missing_transaction_id": {
        "zh": "收据中未找到交易ID",
        "en": "No transaction ID in the receipt",
        "ja": "レシートに取引IDがありません",
        "ko": "영수증에 거래 ID가 없어요",
        "pt": "O recibo não tem ID de transação",
        "es": "El recibo no tiene ID de transacción",
        "id": "Struk tidak punya ID transaksi",
    },
    "missing_transaction_identifier": {
        "zh": "缺少 transaction_identifier",
        "en": "transaction_identifier is required",
        "ja": "transaction_identifier が必要です",
        "ko": "transaction_identifier가 필요해요",
        "pt": "transaction_identifier é obrigatório",
        "es": "Se requiere transaction_identifier",
        "id": "transaction_identifier wajib",
    },
    "missing_google_order": {
        "zh": "缺少 order_id 或 purchase_token",
        "en": "order_id or purchase_token is required",
        "ja": "order_id または purchase_token が必要です",
        "ko": "order_id 또는 purchase_token이 필요해요",
        "pt": "order_id ou purchase_token é obrigatório",
        "es": "Se requiere order_id o purchase_token",
        "id": "order_id atau purchase_token wajib",
    },
    "operation_failed": {
        "zh": "操作失败",
        "en": "Operation failed",
        "ja": "操作に失敗しました",
        "ko": "작업에 실패했어요",
        "pt": "A operação falhou",
        "es": "La operación falló",
        "id": "Operasi gagal",
    },
}


def validation_message(
    key: str,
    accept_language: Optional[str] = None,
    lang: Optional[str] = None,
) -> str:
    table = _VALIDATION.get(key)
    if not table:
        return key
    return _pick_http_msg(table, accept_language=accept_language, lang=lang)


# ===== 方案 B：locale JSON + _("中文原文") =====
# 源码以中文为 msgid；已走 validation_message / prompt pack 的不要再包。
_LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
_ui_lang: ContextVar[str] = ContextVar("ui_lang", default="en")
_catalogs: dict[str, dict[str, str]] | None = None


def _load_locale_catalogs() -> dict[str, dict[str, str]]:
    global _catalogs
    if _catalogs is not None:
        return _catalogs
    loaded: dict[str, dict[str, str]] = {}
    for code in _LOCALE_UI_LANGS:
        path = _LOCALE_DIR / f"{code}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            loaded[code] = {str(k): str(v) for k, v in data.items()}
    _catalogs = loaded
    return loaded


def bind_request_ui_lang(
    *, accept_language: Optional[str] = None, lang: Optional[str] = None
) -> Token:
    """按本次 HTTP 请求绑定 UI 语言，须在 finally 里 reset_ui_lang。"""
    return _ui_lang.set(http_ui_lang(accept_language=accept_language, lang=lang))


def reset_ui_lang(token: Token) -> None:
    _ui_lang.reset(token)


def get_ui_lang() -> str:
    return _ui_lang.get()


def reload_locale_catalogs() -> None:
    """测试或热加载词表时清空缓存。"""
    global _catalogs
    _catalogs = None


def _(message: str, lang: Optional[str] = None) -> str:
    """用当前请求语言翻译接口提示。中文缺条目用原文；其它语种缺条目回退英文，再回退原文。
    可传 lang= 覆盖 ContextVar（WS / 人设输出语种）。"""
    if not message:
        return message
    catalogs = _load_locale_catalogs()
    use_lang = normalize_ui_language(lang) if lang else _ui_lang.get()
    table = catalogs.get(use_lang) or {}
    if message in table:
        return table[message]
    if use_lang == "zh":
        return message
    en_table = catalogs.get("en") or {}
    return en_table.get(message, message)


_ANON_USER_MSGID = "匿名用户"
_USER_PREFIX_MSGID = "用户_"
_VIP_PLAN_MSGIDS = {
    "weekly": "周卡会员",
    "monthly": "月度会员",
    "yearly": "年度会员",
    "lifetime": "终身会员",
}
_RECHARGE_PKG_MSGIDS = {
    "pack_250": "250积分包",
    "pack_550": "550积分包",
    "pack_800": "800积分包",
    "pack_1900": "1900积分包",
}
# benefit_products.title 种子/后台常见中文名 → 与会员/积分包共用 msgid
_BENEFIT_TITLE_MSGIDS = {
    "周卡VIP": "周卡会员",
    "周卡会员": "周卡会员",
    "月度VIP": "月度会员",
    "月度会员": "月度会员",
    "年度VIP": "年度会员",
    "年度会员": "年度会员",
    "终身VIP": "终身会员",
    "终身会员": "终身会员",
    "250金币": "250积分包",
    "250积分包": "250积分包",
    "550金币": "550积分包",
    "550积分包": "550积分包",
    "800金币": "800积分包",
    "800积分包": "800积分包",
    "1900金币": "1900积分包",
    "1900积分包": "1900积分包",
}
_CREDIT_EVENT_MSGIDS = {
    "chat": "对话消耗",
    "create_agent": "创建智能体",
    "agent_daily": "智能体每日消耗",
    "modify_agent": "修改智能体",
}
_CREDIT_DESC_DAILY_GIFT = re.compile(r"^每日VIP赠送 (\d+) 积分$")
_CREDIT_DESC_DAILY_GIFT_CATCHUP = re.compile(r"^补发VIP赠送 (\d+)天共(\d+)积分$")
_CREDIT_DESC_FREE_TURN = re.compile(r"^免费轮次 \(剩余(\d+)\)$")
_CREDIT_DESC_FREE_TURN_REFUND = re.compile(r"^免费轮次退回 \(剩余(\d+)\)$")
_CREDIT_DESC_VIP_OPEN = re.compile(r"^开通VIP (\w+)，赠送(\d+)积分$")
_CREDIT_DESC_RECHARGE = re.compile(r"^充值 (\S+)，获得(\d+)积分$")
_CREDIT_DESC_REFUND = re.compile(r"^退回 (\d+) 积分$")
_CREDIT_DESC_AGENT_DAILY = re.compile(r"^智能体每日消耗 x(\d+)$")
FEEDBACK_ENTRY_MSGID = "意见反馈"
FEEDBACK_WELCOME_MSGID = "很高兴听到你的反馈。有任何想法、建议或遇到的问题，都可以告诉我们！"
FEEDBACK_AUTO_REPLY_MSGID = "感谢你的反馈！我们已收到消息，会认真查看。"
FEEDBACK_AUTO_REPLY_MSGID_LEGACY = "您的问题已收到，我们会尽快处理"
_FEEDBACK_COPY_MSGIDS = (
    FEEDBACK_ENTRY_MSGID,
    FEEDBACK_WELCOME_MSGID,
    FEEDBACK_AUTO_REPLY_MSGID,
    FEEDBACK_AUTO_REPLY_MSGID_LEGACY,
)


def localize_fallback_user_name(name: Optional[str], lang: Optional[str] = None) -> str:
    """读侧翻译落库的「匿名用户」「用户_xxxx」，真昵称原样返回。"""
    raw = (name or "").strip()
    if not raw or raw == _ANON_USER_MSGID:
        return _(_ANON_USER_MSGID, lang=lang)
    if raw.startswith(_USER_PREFIX_MSGID):
        return _("用户_{id}", lang=lang).format(id=raw[len(_USER_PREFIX_MSGID):])
    return raw


def vip_plan_display_name(plan_type: str, lang: Optional[str] = None) -> str:
    msgid = _VIP_PLAN_MSGIDS.get(plan_type or "")
    return _(msgid, lang=lang) if msgid else (plan_type or "")


def recharge_package_display_name(package_key: str, lang: Optional[str] = None) -> str:
    msgid = _RECHARGE_PKG_MSGIDS.get(package_key or "")
    return _(msgid, lang=lang) if msgid else (package_key or "")


def benefit_product_display_name(
    *,
    product_type: str = "",
    period: str = "",
    store_product_id: str = "",
    apple_product_id: str = "",
    title: str = "",
    lang: Optional[str] = None,
) -> str:
    """福利商品展示名：按 period / pack key / 中文 title 映射，再走 locale（zh/en/ja/ko/pt/es/id）。"""
    ptype = (product_type or "").strip().lower()
    period_key = (period or "").strip().lower()
    store_key = (store_product_id or "").strip()
    title_raw = (title or "").strip()

    if ptype == "subscribe" or period_key in _VIP_PLAN_MSGIDS:
        name = vip_plan_display_name(period_key, lang=lang)
        if name and name != period_key:
            return name

    pkg_key = store_key
    if pkg_key not in _RECHARGE_PKG_MSGIDS and store_key.startswith("pack_"):
        pkg_key = store_key
    if pkg_key in _RECHARGE_PKG_MSGIDS or ptype == "point":
        name = recharge_package_display_name(pkg_key, lang=lang)
        if name and name != pkg_key:
            return name

    msgid = _BENEFIT_TITLE_MSGIDS.get(title_raw)
    if msgid:
        return _(msgid, lang=lang)
    if title_raw:
        translated = _(title_raw, lang=lang)
        return translated or title_raw
    return store_key or apple_product_id or period_key or ""


def credit_event_display_name(event_type: str, lang: Optional[str] = None) -> str:
    msgid = _CREDIT_EVENT_MSGIDS.get(event_type or "")
    return _(msgid, lang=lang) if msgid else (event_type or "")


def localize_credit_description(description: str, lang: Optional[str] = None) -> str:
    """账单 description 落库保持中文模板，接口读出时再翻。"""
    desc = description or ""
    if not desc:
        return desc
    m = _CREDIT_DESC_DAILY_GIFT.fullmatch(desc)
    if m:
        return _("每日VIP赠送 {n} 积分", lang=lang).format(n=m.group(1))
    m = _CREDIT_DESC_DAILY_GIFT_CATCHUP.fullmatch(desc)
    if m:
        return _("补发VIP赠送 {days}天共{n}积分", lang=lang).format(
            days=m.group(1), n=m.group(2),
        )
    m = _CREDIT_DESC_FREE_TURN.fullmatch(desc)
    if m:
        return _("免费轮次 (剩余{n})", lang=lang).format(n=m.group(1))
    m = _CREDIT_DESC_FREE_TURN_REFUND.fullmatch(desc)
    if m:
        return _("免费轮次退回 (剩余{n})", lang=lang).format(n=m.group(1))
    m = _CREDIT_DESC_VIP_OPEN.fullmatch(desc)
    if m:
        return _("开通VIP {plan}，赠送{n}积分", lang=lang).format(
            plan=vip_plan_display_name(m.group(1), lang=lang),
            n=m.group(2),
        )
    m = _CREDIT_DESC_RECHARGE.fullmatch(desc)
    if m:
        return _("充值 {package}，获得{n}积分", lang=lang).format(
            package=recharge_package_display_name(m.group(1), lang=lang),
            n=m.group(2),
        )
    m = _CREDIT_DESC_REFUND.fullmatch(desc)
    if m:
        return _("退回 {n} 积分", lang=lang).format(n=m.group(1))
    m = _CREDIT_DESC_AGENT_DAILY.fullmatch(desc)
    if m:
        return _("智能体每日消耗 x{n}", lang=lang).format(n=m.group(1))
    return _(desc, lang=lang)


def localize_feedback_auto_reply(content: str, lang: Optional[str] = None) -> str:
    raw = content or ""
    if raw in _FEEDBACK_COPY_MSGIDS:
        return _(raw, lang=lang)
    return raw
