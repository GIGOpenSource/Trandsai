# 后端多语言字典 —— 供 core/config.py 及各服务模块导入使用

_SUPPORTED_UI_LANGS = frozenset({"zh", "en", "ja", "ko", "pt", "es", "id"})


def normalize_ui_language(lang: str | None) -> str:
    """将浏览器 / i18next 传入值规范为 zh|en|ja|ko|pt|es|id。
    - zh-CN / zh-Hans → zh；en-US → en
    - 不识别的语种回退 en（与客户端 fallbackLng 一致），避免误入中文提示词。
    """
    if not lang:
        return "zh"
    raw = str(lang).strip().replace("_", "-")
    lower = raw.lower()
    if lower.startswith("zh"):
        return "zh"
    base = raw.split("-")[0].lower()
    if base in _SUPPORTED_UI_LANGS:
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
    "sampai nanti", "aku sibuk", "aku pergi dulu", "kita ngobrol nanti",
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
}

# 多条并入一轮时，附加在发给 LLM 的用户输入前的说明（勿改「编号」行格式过多，便于模型解析）
_MULTI_MESSAGE_AGENT_PREFIX = {
    "zh": "【对方刚刚连续发送了以下多条消息。请先自然接住最后一条里最关键的一句（可简短点一下“你刚说的××”），再联系全文与上文语境，用通顺的口语整体回复，不要对每条逐条机械应答、也不要像读清单】\n\n",
    "en": "[They just sent several messages in a row below. First pick up the last/strongest line (e.g. nod to what they just said), then read everything with context and reply **once** in a natural, spoken way—no bullet-by-bullet robot answers, no “reading a list” tone.]\n\n",
    "ja": "【相手が続けて複数送っています。最後の一言を先に受け止め（「さっきの××」のように短く触れてもよい）から、全文と文脈を踏まえ自然に**一度**返してください。行ごとに機械的に答えたり、箇条書きのように聞かないでください】\n\n",
    "ko": "【상대가 연속으로 여러 메시지를 보냈습니다. 가장 최근/핵심 말을 먼저 받아주고(“방금 말한 ○○” 정도), 전체·맥락을 보고 **한 번**에 자연스럽게 답하세요. 줄마다 기계적으로 답하거나 목록 읽듯 말하지 마세요】\n\n",
    "pt": "[Várias mensagens seguidas. Primeiro acolhe a última/mais forte, depois leia tudo com o contexto e responda **uma vez** de forma natural—sem estilo de lista.]\n\n",
    "es": "[Varios mensajes seguidos. Primero recoge la última idea (puedes nombrar “lo que acabas de decir…”), luego con contexto responde **una vez** y de forma natural; sin leer un listado.]\n\n",
    "id": "[Beberapa pesan berurutan. Sambut dulu yang paling terakhir/kuat, baca semua dengan konteks, lalu balas **sekali** dengan wajar—jangan kaya baca daftar.]\n\n",
}

_AGENT_TIMEOUT_MESSAGE = {
    "zh": "生成回复超时，请稍后再试",
    "en": "Reply timed out. Please try again.",
    "ja": "応答がタイムアウトしました。もう一度お試しください",
    "ko": "응답 시간이 초과됐어요. 다시 시도해 주세요",
    "pt": "Tempo esgotado. Tente novamente.",
    "es": "Tiempo de espera agotado. Inténtalo de nuevo.",
    "id": "Waktu habis. Coba lagi.",
}

_CONNECT_MESSAGES = {
    "zh": lambda name: f"已连接到 {name}，开始聊天吧~",
    "en": lambda name: f"Connected to {name}. Let's chat~",
    "ja": lambda name: f"{name}に接続しました。お話ししましょう~",
    "ko": lambda name: f"{name}에 연결됐어. 이야기하자~",
    "pt": lambda name: f"Conectado a {name}. Vamos conversar~",
    "es": lambda name: f"Conectado a {name}. ¡Hablemos~!",
    "id": lambda name: f"Terhubung dengan {name}. Mari ngobrol~",
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
}

_WS_COMPANION_NOT_FOUND = {
    "zh": "智能体不存在，请先创建",
    "en": "This companion doesn’t exist yet. Please create one first.",
    "ja": "このコンパニオンはまだありません。先に作成してください",
    "ko": "해당 AI가 없습니다. 먼저 만들어 주세요",
    "pt": "Este companheiro não existe. Crie um primeiro.",
    "es": "Este compañero no existe. Créalo primero.",
    "id": "Teman AI ini belum ada. Buat dulu ya.",
}

_WS_ACCESS_DENIED = {
    "zh": "无权访问该智能体",
    "en": "You don’t have access to this companion.",
    "ja": "このコンパニオンにアクセスできません",
    "ko": "이 AI에 접근할 수 없습니다",
    "pt": "Sem permissão para acessar este companheiro.",
    "es": "No tienes acceso a este compañero.",
    "id": "Kamu tidak punya akses ke teman AI ini.",
}

_WS_CHAT_UNEXPECTED_ERROR = {
    "zh": "聊天服务出现异常，请稍后重试",
    "en": "Something went wrong. Please try again later.",
    "ja": "チャットで問題が発生しました。しばらくしてからお試しください",
    "ko": "채팅에 문제가 생겼어요. 잠시 후 다시 시도해 주세요",
    "pt": "Algo deu errado no chat. Tente de novo mais tarde.",
    "es": "Hubo un problema en el chat. Inténtalo más tarde.",
    "id": "Terjadi masalah di obrolan. Coba lagi nanti.",
}
