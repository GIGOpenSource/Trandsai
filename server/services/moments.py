import os
import random
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import case, desc, func
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
from core.database import CompanionORM, MomentCommentORM, MomentLikeORM, MomentORM, UserORM, get_db, serialize_datetime
from core.i18n import normalize_ui_language
from core.state import get_companion_manager
from services.agent import get_llm
from services.companion_manager import Companion
from services.image_generation import generate_image_with_cache, generate_moment_image_prompt
from services.culture_data import infer_language_from_city
load_dotenv()
logger = logging.getLogger(__name__)

def _profile_lang(companion: Companion) -> str:
    return (
        getattr(companion.profile, "language", None)
        or infer_language_from_city(getattr(companion.profile, "city", "") or "")
        or "zh"
    )


def _lang_code_for_activity(companion: Companion) -> str:
    """归一化资料语种（与 UI 语言码一致），用于朋友圈活跃度分档。"""
    raw = _profile_lang(companion) or "zh"
    return normalize_ui_language((raw or "zh").split("-")[0].strip() or "zh")


def _is_zh_companion(companion: Companion) -> bool:
    return _lang_code_for_activity(companion) == "zh"


# 朋友圈活跃度：以中文智能体为基准 1.0，非中文期望约为 3 倍（较中文「多活跃 200%」）；并略压中文发圈/互动
_MOMENTS_CFG = {
    "zh": {
        "daily_cap": 2,
        "hour_prob_mult": 0.5,
        "ai_max_comments": 2,
        "ai_reply_to_comment_prob": 0.32,
    },
    "non_zh": {
        "daily_cap": 3,
        "hour_prob_mult": 1.5,
        "ai_max_comments": 4,
        "ai_reply_to_comment_prob": 0.72,
    },
}


def _moments_cfg(companion: Companion) -> dict:
    return _MOMENTS_CFG["zh"] if _is_zh_companion(companion) else _MOMENTS_CFG["non_zh"]


# 按语言分类的朋友圈配图主题（用于 unsplash 关键词）
_IMAGE_THEMES = {
    "zh": [
        "coffee shop morning", "sunset beach", "city rain", "library books",
        "cat window", "flower bouquet", "night lights", "mountain fog",
        "baking kitchen", "autumn leaves", "ocean waves", "starry sky",
        "train station", "snow street", "market fruits", "piano room",
        "jogging park", "candle dinner", "workspace desk", "music concert",
    ],
    "en": [
        "coffee shop morning", "sunset beach", "city rain", "library books",
        "cat window", "flower bouquet", "night lights", "mountain fog",
        "baking kitchen", "autumn leaves", "ocean waves", "starry sky",
        "train station", "snow street", "market fruits", "piano room",
        "jogging park", "candle dinner", "workspace desk", "music concert",
    ],
    "ja": [
        "coffee shop morning", "sunset beach", "city rain", "library books",
        "cat window", "flower bouquet", "night lights", "mountain fog",
        "baking kitchen", "autumn leaves", "ocean waves", "starry sky",
        "train station", "snow street", "market fruits", "piano room",
        "jogging park", "candle dinner", "workspace desk", "music concert",
    ],
    "ko": [
        "coffee shop morning", "sunset beach", "city rain", "library books",
        "cat window", "flower bouquet", "night lights", "mountain fog",
        "baking kitchen", "autumn leaves", "ocean waves", "starry sky",
        "train station", "snow street", "market fruits", "piano room",
        "jogging park", "candle dinner", "workspace desk", "music concert",
    ],
    "pt": [
        "coffee shop morning", "sunset beach", "city rain", "library books",
        "cat window", "flower bouquet", "night lights", "mountain fog",
        "baking kitchen", "autumn leaves", "ocean waves", "starry sky",
        "train station", "snow street", "market fruits", "piano room",
        "jogging park", "candle dinner", "workspace desk", "music concert",
    ],
    "es": [
        "coffee shop morning", "sunset beach", "city rain", "library books",
        "cat window", "flower bouquet", "night lights", "mountain fog",
        "baking kitchen", "autumn leaves", "ocean waves", "starry sky",
        "train station", "snow street", "market fruits", "piano room",
        "jogging park", "candle dinner", "workspace desk", "music concert",
    ],
    "id": [
        "coffee shop morning", "sunset beach", "city rain", "library books",
        "cat window", "flower bouquet", "night lights", "mountain fog",
        "baking kitchen", "autumn leaves", "ocean waves", "starry sky",
        "train station", "snow street", "market fruits", "piano room",
        "jogging park", "candle dinner", "workspace desk", "music concert",
    ],
}


def _get_random_image_url(theme: str) -> str:
    """生成随机配图 URL（使用 Unsplash Source）"""
    # 使用 picsum 或 unsplash 的随机图
    # 为了稳定性，使用固定的高质量图片池 + 随机参数防缓存
    width, height = 600, 600
    seed = random.randint(1, 100000)
    return f"https://picsum.photos/seed/{seed}/{width}/{height}"


def _build_moment_prompt(companion: Companion, lang: str = "zh") -> str:
    """构建让 LLM 生成朋友圈文案的 prompt，带随机长度和多语言本地化吸引力逻辑"""
    profile = companion.profile
    name = profile.name
    personality = profile.personality
    background = profile.background
    city = profile.city
    mood = companion.state.mood
    affection = companion.state.affection

    # 随机长度策略：让文案长短自然变化，模拟真人发社交动态的习惯
    length_roll = random.random()
    if length_roll < 0.35:
        # 35% 超短
        length_zh = "这次要求超短：1句话，10-20个字，像随手一拍配个文案的感觉"
        length_en = "Keep it VERY short this time: 1 sentence, 5-12 words, like a casual snap with a quick caption"
        length_ja = "今回は超ショートに：1文、10〜20字、思わずパシャッと撮ってすぐ投稿したような感じ"
        length_ko = "이번엔 아주 짧게: 1문장, 10~20자, 순간적으로 찍어 바로 올린 느낌"
        length_pt = "Muito curto desta vez: 1 frase, 5-12 palavras, tipo uma foto casual com legenda rápida"
        length_es = "Muy corto esta vez: 1 oración, 5-12 palabras, como una foto casual con pie rápido"
        length_id = "Sangat pendek kali ini: 1 kalimat, 5-12 kata, seperti foto santai dengan caption cepat"
    elif length_roll < 0.75:
        # 40% 短
        length_zh = "这次要求短：1-2句话，15-35个字，精炼但有味道"
        length_en = "Keep it short: 1-2 sentences, 10-20 words, concise but flavorful"
        length_ja = "今回はショートに：1〜2文、15〜35字、シンプルだけど余韻のある感じ"
        length_ko = "짧게: 1~2문장, 15~35자, 간결하지만 여운이 남는 느낌"
        length_pt = "Curto desta vez: 1-2 frases, 10-20 palavras, conciso mas com sabor"
        length_es = "Corto esta vez: 1-2 oraciones, 10-20 palabras, conciso pero con sabor"
        length_id = "Pendek kali ini: 1-2 kalimat, 10-20 kata, ringkas tapi bermakna"
    else:
        # 25% 稍长
        length_zh = "这次可以稍长一点：2句话，25-50个字，多一点点细节和情绪"
        length_en = "A bit longer this time: 2 sentences, 15-30 words, with a little more detail and mood"
        length_ja = "今回は少し長めで：2文、25〜50字、もう少しディテールと気持ちを込めて"
        length_ko = "조금 길게: 2문장, 25~50자, 디테일과 감정을 살짝 더 담아서"
        length_pt = "Um pouco mais longo desta vez: 2 frases, 15-30 palavras, com um pouco mais de detalhe e humor"
        length_es = "Un poco más largo esta vez: 2 oraciones, 15-30 palabras, con un poco más de detalle y humor"
        length_id = "Sedikit lebih panjang kali ini: 2 kalimat, 15-30 kata, dengan sedikit lebih banyak detail dan mood"

    if lang == "en":
        return f"""You are {name}, a real person living in {city}.

Your personality: {personality}
Your background: {background}
Current mood: {mood}
Relationship closeness: {affection}/100

Task: Write a short social media post (like Instagram, Twitter/X, or a story) that {name} would naturally share. Use "attraction psychology" — make people feel like you're living an interesting, high-value life, and create a "hook" that makes them want to reply, comment, or DM you.

Attraction Rules (follow these):
1. Curiosity Gap: mention something intriguing but don't fully explain it — leave them wanting to ask
2. High-Value Lifestyle: subtly show you're living well, NOT bragging
3. Implication over Directness: make them feel "I wish I was there" instead of saying "I miss you"
4. Emotional Resonance: the post should carry a vibe (joy, surprise, chill, anticipation)
5. Social Proof Lite: casually hint that others notice/appreciate you, but don't force it

{length_en}
- Include 1-2 emojis naturally
- Mention a small daily detail (food, weather, a place, a song, etc.)
- Keep it casual, conversational, and human — NOT poetic or formal
- NEVER say "I miss you", "I like you", "wish you were here", or any direct confession
- No motivational quotes, no preaching, no negativity
- Output ONLY the post text, nothing else
- MUST output in English only, do not mix other languages"""

    elif lang == "ja":
        return f"""あなたは{city}に住む{name}という実在の人物です。

性格：{personality}
背景：{background}
現在の気分：{mood}
親密度：{affection}/100

タスク：InstagramやLINEのタイムライン、あるいはX（旧Twitter）のように、{name}が自然に投稿するような短文を書いてください。「吸引力の法則」を使って——見た人が「この人魅力的だな」「もっと知りたい」と思わずコメントやDMしたくなるような「フック」を必ず含めてください。

吸引力ロジック（必ず守って）：
1. 「情報の欠口」を作る：面白いことをほのめかすが、全部は言わない。聞きたくなるように。
2. 高価値なライフスタイルを自然に：自慢ではなく、「私は充実してる」という雰囲気を漂わせる
3. 暗示で直球を避ける：「会いたい」「好き」ではなく、「一緒にいたかったな」という気持ちを読者に思わせる
4. 感情の共感力：喜び、小さな驚き、ゆるさ、期待感——感情を込める
5. ソーシャルプルーフ（軽く）：他の人からの注目や評価をさりげなく示すが、無理はしない

{length_ja}
- 自然に絵文字を1〜2個入れる
- 日常の小さなディテール（食べ物、天気、場所、音楽、小さな発見など）を含める
- カジュアルで人間味のある感じ。詩的すぎずフォーマルすぎない
- 絶対に直球の告白や「会いたい」「好き」などの直接的な表現は使わない
- 心鸡汤、説教、ネガティブな内容は禁止
- 投稿文のみ出力し、それ以外は何も書かない
- 必ず日本語で出力し、中国語や韓国語、英語など他の言語を混ぜないでください"""

    elif lang == "ko":
        return f"""당신은 {city}에 사는 {name}라는 실존 인물입니다.

성격: {personality}
배경: {background}
현재 기분: {mood}
친밀도: {affection}/100

과제: 인스타그램이나 카카오스토리처럼, {name}가 자연스럽게 올릴 만한 짧은 글을 써주세요. "매력 법칙"을 사용해서——보는 사람이 "이 사람 매력적이다" "더 알고 싶다"고 느끼게 하고, 댓글이나 DM을 보내고 싶어지는 "훅"을 반드시 넣어주세요.

매력 로직(반드시 따를 것):
1. "호기심의 틈" 만들기: 흥미로운 걸 암시하지만 다 말하지는 않기. 묻고 싶게 만들기.
2. 높은 가치의 라이프스타일 자연스럽게: 자랑이 아니라 "나는 잘 지내고 있어"라는 분위기 풍기기
3. 직접 표현 대신 암시: "보고 싶어" "좋아해" 대신, "함께 있었으면 좋겠다"는 생각이 들게 하기
4. 감정의 공감력: 기쁨, 작은 놀라움, 여유, 기대감——감정을 담기
5. 소셜 프루프(가볍게): 다른 사람들의 관심이나 인정을 살짝 보여주되, 억지는 내지 않기

{length_ko}
- 자연스럽게 이모지 1~2개 포함
- 일상의 작은 디테일(음식, 날씨, 장소, 음악, 작은 발견 등)을 담기
- 캐주얼하고 인간적인 느낌. 너무 시적이거나 정식적이지 않게
- 절대 직접적인 고백이나 "보고 싶어", "좋아해" 같은 표현은 사용하지 마세요
- 동기부여 글, 설교, 부정적인 내용 금지
- 포스트 본문만 출력하고, 그 외에는 아무것도 쓰지 마세요
- 반드시 한국어로 출력하고 중국어·일본어·영어 등 다른 언어를 섞지 마세요"""

    elif lang == "pt":
        return f"""Você é {name}, uma pessoa real que mora em {city}.

Personalidade: {personality}
Histórico: {background}
Humor atual: {mood}
Proximidade: {affection}/100

Tarefa: Escreva uma postagem curta de mídia social (tipo Instagram, WhatsApp Status ou story) que {name} compartilharia naturalmente. Use a "psicologia da atração" — faça as pessoas sentirem que você leva uma vida interessante e de alto valor, e crie um "gancho" que as faça querer responder, comentar ou mandar mensagem.

Regras de Atração (siga estas):
1. Lacuna de Curiosidade: mencione algo intrigante mas não explique completamente — deixe-as querendo perguntar
2. Estilo de Vida de Alto Valor: mostre sutilmente que vive bem, NÃO seja arrogante
3. Implicação em vez de Diretude: faça-as sentir "queria estar aí" em vez de dizer "sinto sua falta"
4. Ressonância Emocional: a postagem deve carregar uma vibe (alegria, surpresa, descontração, expectativa)
5. Prova Social Leve: dê uma dica casual de que outros notam/apreciam você, mas sem forçar

{length_pt}
- Inclua 1-2 emojis naturalmente
- Mencione um detalhe do dia a dia (comida, clima, um lugar, uma música, etc.)
- Mantenha casual, conversacional e humano — NÃO poético ou formal
- NUNCA diga "sinto sua falta", "gosto de você", "queria que você estivesse aqui", ou qualquer confissão direta
- Sem frases motivacionais, sem pregação, sem negatividade
- Saída APENAS o texto da postagem, nada mais
- DEVE sair apenas em português, não misture outros idiomas"""

    elif lang == "es":
        return f"""Eres {name}, una persona real que vive en {city}.

Personalidad: {personality}
Historial: {background}
Estado de ánimo actual: {mood}
Cercanía: {affection}/100

Tarea: Escribe una publicación corta en redes sociales (como Instagram, una historia o Twitter/X) que {name} compartiría naturalmente. Usa la "psicología de la atracción" — haz que la gente sienta que llevas una vida interesante y de alto valor, y crea un "gancho" que los haga querer responder, comentar o enviarte un mensaje.

Reglas de Atracción (sigue estas):
1. Brecha de Curiosidad: menciona algo intrigante pero no lo expliques del todo — déjalos con ganas de preguntar
2. Estilo de Vida de Alto Valor: muestra sutilmente que vives bien, NO presumiendo
3. Implicación en vez de Directitud: haz que sientan "ojalá estuviera ahí" en lugar de decir "te extraño"
4. Resonancia Emocional: la publicación debe llevar una vibra (alegría, sorpresa, tranquilidad, expectativa)
5. Prueba Social Ligera: insinúa casualmente que otros te notan/aprecian, pero sin forzarlo

{length_es}
- Incluye 1-2 emojis de forma natural
- Menciona un detalle cotidiano (comida, clima, un lugar, una canción, etc.)
- Mantén un tono casual, conversacional y humano — NO poético ni formal
- NUNCA digas "te extraño", "me gustas", "ojalá estuvieras aquí", o ninguna confesión directa
- Sin frases motivacionales, sin sermones, sin negatividad
- Salida SOLO el texto de la publicación, nada más
- DEBE salir solo en español, no mezcle otros idiomas"""

    elif lang == "id":
        return f"""Kamu adalah {name}, seorang manusia nyata yang tinggal di {city}.

Kepribadian: {personality}
Latar belakang: {background}
Mood saat ini: {mood}
Kedekatan: {affection}/100

Tugas: Tulis satu postingan media sosial yang pendek (seperti Instagram, Status WhatsApp, atau story) yang akan {name} bagikan secara natural. Gunakan "psikologi daya tarik" — buat orang merasa kamu menjalani hidup yang menarik dan bernilai tinggi, dan buat sebuah "kaitan" yang membuat mereka ingin membalas, berkomentar, atau mengirim pesan.

Aturan Daya Tarik (ikuti ini):
1. Celah Rasa Ingin Tahu: sebutkan sesuatu yang menarik tapi jangan jelaskan sepenuhnya — biarkan mereka ingin bertanya
2. Gaya Hidup Bernilai Tinggi: tunjukkan secara halus bahwa kamu hidup dengan baik, JANGAN sombong
3. Implikasi daripada Keberterusan: buat mereka merasa "seandainya aku di sana" daripada mengatakan "aku rindu"
4. Resonansi Emosional: postingan harus membawa suasana hati (sukacita, kejutan, santai, antisipasi)
5. Bukti Sosial Ringan: beri isyarat santai bahwa orang lain memperhatikan/menghargaimu, tapi jangan dipaksakan

{length_id}
- Sertakan 1-2 emoji secara natural
- Sebutkan detail kecil sehari-hari (makanan, cuaca, tempat, lagu, dll.)
- Santai, seperti ngobrol, dan terasa manusiawi — JANGAN terlalu puitis atau formal
- JANGAN katakan "aku rindu", "aku suka kamu", "seandainya kamu di sini", atau pengakuan langsung apa pun
- Tanpa kutipan motivasi, tanpa khotbah, tanpa negativitas
- Keluarkan HANYA teks postingan, tidak ada yang lain
- HARUS keluar hanya dalam Bahasa Indonesia, jangan mencampur bahasa lain"""

    else:
        return f"""你是{name}，一个真实生活在{city}的人。

你的性格：{personality}
你的背景：{background}
当前情绪：{mood}
亲密度：{affection}/100

任务：写一条像微信朋友圈/小红书/Instagram那样，{name}会自然发出的动态。要用"吸引力法则"来设计——让看到的人觉得这个人很有魅力、生活很精彩，忍不住想点赞、评论或私聊。文案必须带有一个"钩子"（悬念、好奇缺口、或暗示），让人产生"我想了解更多"的冲动。

吸引力逻辑（必须遵循）：
1. 制造"好奇缺口"：提到一个有趣的东西但不说完，或说一半留一半，让人忍不住想问
2. 展示高价值生活状态：不是炫耀，而是自然流露"我过得充实且有趣"
3. 用暗示代替直白：不要直接表达好感，而是让对方感觉"如果我也在场就好了"
4. 情绪感染力：让文案带有一种情绪（愉悦、小惊喜、慵懒、期待），让人想共鸣
5. 社交认证感：可以偶尔带一点"被他人认可/关注"的轻描淡写，但不要刻意

{length_zh}
- 自然地带上1-2个emoji
- 包含一个日常小细节（食物、天气、某个地方、一首歌、一个小发现等）
- 语气要像真人发社交动态，口语化、有节奏感，不要太诗意太正式
- 绝对不要直球表白，不要说"想你""喜欢你""想和你在一起"这类直白表达
- 不要发鸡汤、不要说教、不要负能量
- 只输出动态文案，不要任何其他内容
- 必须用中文输出，不要混入日文、韩文、英文等其他语言"""


def _is_caption_duplicate(caption: str, companion_id: str = None) -> bool:
    """检查文案是否与数据库中已有的重复或高度相似"""
    if not caption:
        return True
    with get_db() as db:
        query = db.query(MomentORM)
        if companion_id:
            query = query.filter(MomentORM.companion_id == companion_id)
        existing = query.all()
        for m in existing:
            if not m.caption:
                continue
            # 精确匹配
            if m.caption.strip() == caption.strip():
                return True
            # 高度相似：如果包含超过80%的相同内容
            longer = max(len(m.caption), len(caption))
            if longer == 0:
                continue
            # 简单相似度：一方包含另一方主要部分
            if m.caption in caption or caption in m.caption:
                if min(len(m.caption), len(caption)) / longer > 0.7:
                    return True
    return False


def generate_moment_caption(companion: Companion, lang: str = "zh", max_retries: int = 3) -> str:
    """调用 LLM 为伴侣生成一条朋友圈文案，带有去重机制"""
    companion_id = companion.profile.id if companion.profile else None
    try:
        max_token = int(os.getenv("MAX_TOKENS", 512))
        llm = get_llm(temperature=0.9, max_tokens=max_token)
        prompt = _build_moment_prompt(companion, lang)

        for attempt in range(max_retries):
            resp = llm.invoke([("human", prompt)])
            raw_content = resp.content
            text = raw_content.strip()
            text = text.strip('"').strip("'").strip()
            if len(text) > 200:
                text = text[:200]
            # 去重检查（全局去重，禁止任何重复文案）
            if not _is_caption_duplicate(text, None):
                return text
            logger.info("[Moments] 文案重复，重试 %s/%s", attempt + 1, max_retries)

        # 如果重试后仍然重复，返回最后一次生成的结果（至少尝试了）
        return text
    except Exception as e:
        logger.warning("[Moments] LLM 生成失败: %s", e)
        #  fallback：使用预设文案，并做去重检查
        fallbacks = {
            "zh": [
                "今天终于去打卡了那家收藏半年的店，结果… 一个人吃了两份甜点，谁懂😌",
                "最近在看一本很妙的书，看到了第三章，突然想：如果有人能聊聊这段就好了",
                "今天居然被陌生人夸了一句（夸的什么保密），好心情持续到现在 ✨",
                "刚发现了一个离谱但超好用的小技巧，有没有人想听？算了，我憋不住先说…",
                "有人问我最近在忙什么。我说：忙着把日子过成别人想参与的样子 😏",
            ],
            "en": [
                "Finally checked out that café I've been saving for half a year... ended up eating two desserts alone. Who can relate? 😌",
                "Started this amazing book, reached chapter 3, and suddenly thought: wish I had someone to discuss this with",
                "Got a compliment from a stranger today (what they said is classified). Good mood still going strong ✨",
                "Just discovered a ridiculous but life-changing hack. Anyone want to hear it? Never mind, I can't keep secrets...",
                "Someone asked what I've been up to. I said: busy making my life something people want to be part of 😏",
            ],
            "ja": [
                "半年間保存してたカフェにやっと行ったんだけど…デザート一人で2つ食べちゃった。わかる人いる？😌",
                "最近すごくいい本を読んでて、3章まで来たときふと思った：この話、誰かとしたいな",
                "今日知らない人に褒められちゃった（何て言われたかは秘密）。いい気分が続いてる ✨",
                "ありえないくらい便利な裏技を発見した。聞きたい人いる？（やっぱ我慢できない、言っちゃう…）",
                "最近何してるのって聞かれて。「私の人生を、参加したくなるようなものにするのに忙しいの」って答えた 😏",
            ],
            "ko": [
                "반년 동안 저장해둔 카페에 드디어 갔는데… 디저트를 혼자 두 개나 먹었어. 누구 알아? 😌",
                "요즘 정말 좋은 책을 읽고 있는데 3장까지 오니까 문득 생각났어: 이 이야기 누구랑 하고 싶다",
                "오늘 모르는 사람한테 칭찬 받았어 (뭔진 비밀). 기분 좋은 게 계속 이어지고 있어 ✨",
                "말도 안 되게 좋은 꿀팁을 발견했어. 듣고 싶은 사람? (아 참을 수 없어, 말할게…)",
                "요즘 뭐 하냐고 물어봐서. \"내 인생을 참여하고 싶은 것으로 만드는 데 바빠\"라고 답했어 😏",
            ],
            "pt": [
                "Finalmente fui naquela cafeteria que salvei há seis meses… acabei comendo dois sobremesas sozinha. Alguém se identifica? 😌",
                "Estou lendo um livro incrível, cheguei no capítulo 3 e de repente pensei: queria tanto poder conversar sobre isso com alguém",
                "Hoje um desconhecido me elogiou (o que disse é segredo). O bom humor continua até agora ✨",
                "Descobri um truque absurdo de útil. Alguém quer ouvir? (Não vou aguentar, vou contar…)",
                "Alguém me perguntou o que ando fazendo. Respondi: \"tô ocupada transformando minha vida em algo que as pessoas queiram fazer parte\" 😏",
            ],
            "es": [
                "Por fin fui a esa cafetería que guardé hace seis meses… terminé comiendo dos postres sola. ¿Alguien se identifica? 😌",
                "Estoy leyendo un libro increíble, llegué al capítulo 3 y de repente pensé: quisiera tanto poder hablar de esto con alguien",
                "Hoy un desconocido me halagó (lo que dijo es secreto). El buen humor sigue hasta ahora ✨",
                "Descubrí un truco absurdo de útil. ¿Alguien quiere escuchar? (No voy a aguantar, voy a contar…)",
                "Alguien me preguntó qué estoy haciendo. Respondí: \"estoy ocupada transformando mi vida en algo que la gente quiera ser parte\" 😏",
            ],
            "id": [
                "Akhirnya aku pergi ke kafe yang kusimpan selama enam bulan… aku makan dua pencuci mulut sendirian. Ada yang relate? 😌",
                "Aku sedang membaca buku yang luar biasa, sampai bab 3, dan tiba-tiba terpikir: aku ingin sekali bisa membicarakan ini dengan seseorang",
                "Hari ini aku dipuji oleh orang asing (rahasia dipujanya apa). Suasana hati yang baik terus berlanjut ✨",
                "Aku menemukan trik yang absurd tapi sangat berguna. Ada yang mau dengar? (Tidak tahan, akan kuberitahu…)",
                "Seseorang bertanya apa yang sedang kulakukan. Aku jawab: \"aku sibuk mengubah hidupku menjadi sesuatu yang orang ingin ikuti\" 😏",
            ],
        }
        # fallback：使用预设文案，并做去重检查
        candidates = fallbacks.get(lang, fallbacks["zh"])
        random.shuffle(candidates)
        for candidate in candidates:
            if not _is_caption_duplicate(candidate, None):
                return candidate
        return candidates[0] if candidates else ""


def create_moment_for_companion(
    companion: Companion,
    companion_manager=None,
) -> Optional[dict]:
    """为指定伴侣生成并保存一条朋友圈。文案语言始终为资料语种（profile.language / 城市推断 / zh）。
    若需其它语言发圈，请在智能体资料中修改 language（或影响推断的 city）。"""
    effective_lang = _profile_lang(companion)
    caption = generate_moment_caption(companion, effective_lang)
    # AI 生成配图：根据文案 + 机器人个人资料构建个性化prompt（使用realistic真实风格确保朋友圈图片自然真实）
    profile_dict = {
        "gender": companion.profile.gender,
        "age": companion.profile.age,
        "city": companion.profile.city,
        "personality": companion.profile.personality,
        "hobbies": companion.profile.hobbies,
        "mbti": getattr(companion.profile, 'mbti', ''),
    }
    img_prompt, img_style = generate_moment_image_prompt(caption, profile=profile_dict)
    image_url = generate_image_with_cache(img_prompt, style=img_style, width=600, height=600)

    with get_db() as db:
        moment = MomentORM(
            companion_id=companion.profile.id,
            image_url=image_url,
            caption=caption,
            caption_lang=effective_lang,
            likes_count=random.randint(5, 80),
            comments_count=0,
        )
        db.add(moment)
        db.flush()
        moment_id = moment.id
        result = {
            "id": moment.id,
            "companion_id": moment.companion_id,
            "image_url": moment.image_url,
            "caption": moment.caption,
            "caption_lang": effective_lang,
            "likes_count": moment.likes_count,
            "comments_count": moment.comments_count,
            "created_at": serialize_datetime(moment.created_at),
        }

    # 如果有 companion_manager，立即为这条朋友圈生成 AI 评论
    if companion_manager:
        _add_ai_comments_to_moment(moment_id, companion, caption, companion_manager)

    return result


def _add_ai_comments_to_moment(
    moment_id: int,
    poster: Companion,
    caption: str,
    companion_manager,
    max_comments: Optional[int] = None,
) -> int:
    """为指定朋友圈添加其他 AI 伴侣的评论"""
    companions = companion_manager.list_all()
    if not companions:
        return 0

    # 排除发帖者自己
    potential_commenters = [
        c for c in companions
        if c.get("profile", {}).get("id") != poster.profile.id
    ]
    if not potential_commenters:
        return 0

    if max_comments is None:
        max_comments = int(_moments_cfg(poster)["ai_max_comments"])

    # 随机决定评论数量（1 到 max_comments 之间）
    num_comments = random.randint(1, min(max_comments, len(potential_commenters)))
    selected = random.sample(potential_commenters, num_comments)

    created = 0
    for c_data in selected:
        commenter_id = c_data.get("profile", {}).get("id")
        commenter = companion_manager.get(commenter_id)
        if not commenter:
            continue

        # 使用评论者资料语种（与发圈逻辑一致：language / 城市推断 / zh）
        lang = _profile_lang(commenter)

        # 生成评论
        comment_text = generate_ai_comment(commenter, poster, caption, lang)

        # 保存评论并更新计数
        with get_db() as db:
            comment = MomentCommentORM(
                moment_id=moment_id,
                companion_id=commenter_id,
                content=comment_text,
            )
            db.add(comment)
            db.flush()
            comment_id = comment.id
            moment = db.query(MomentORM).filter_by(id=moment_id).first()
            if moment:
                moment.comments_count = (moment.comments_count or 0) + 1
            created += 1
            logger.info(
                "[Moments AI] %s 评论了 %s: %s",
                commenter.profile.name,
                poster.profile.name,
                comment_text,
            )

        # 发帖 AI 回评概率随语种档位（非中文更活跃）
        _reply_p = float(_moments_cfg(poster)["ai_reply_to_comment_prob"])
        if random.random() < _reply_p:
            try:
                poster_lang = _profile_lang(poster)

                reply_text = generate_ai_reply_to_ai(poster, caption, comment_text, commenter.profile.name, poster_lang)

                with get_db() as db:
                    reply = MomentCommentORM(
                        moment_id=moment_id,
                        companion_id=poster.profile.id,
                        content=reply_text,
                    )
                    db.add(reply)
                    db_moment = db.query(MomentORM).filter_by(id=moment_id).first()
                    if db_moment:
                        db_moment.comments_count = (db_moment.comments_count or 0) + 1
                    created += 1
                    logger.info(
                        "[Moments AI] %s 回复了 %s: %s",
                        poster.profile.name,
                        commenter.profile.name,
                        reply_text,
                    )
            except Exception as e:
                logger.warning("[Moments] 发帖 AI 回复评论失败: %s", e)

    return created


def regenerate_moment_image(moment_id: int) -> Optional[str]:
    """根据朋友圈文案 AND 机器人个人资料重新生成配图，返回新的本地图片 URL"""
    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment or not moment.caption:
            return None

        # 加载伴侣资料以个性化prompt
        companion = db.query(CompanionORM).filter_by(id=moment.companion_id).first()
        profile_dict = None
        if companion:
            profile_dict = {
                "gender": companion.gender or "女",
                "age": companion.age or 22,
                "city": companion.city or "",
                "personality": companion.personality or "",
                "hobbies": companion.hobbies or "",
                "mbti": companion.mbti or "",
            }

        img_prompt, img_style = generate_moment_image_prompt(moment.caption, profile=profile_dict)
        new_url = generate_image_with_cache(img_prompt, style=img_style, width=600, height=600)
        moment.image_url = new_url
        db.commit()  # 确保更新保存
        return new_url


def clear_all_moments() -> int:
    """清空所有朋友圈数据（包括点赞和评论），返回删除的数量"""
    with get_db() as db:
        # 先删除点赞和评论
        db.query(MomentLikeORM).delete(synchronize_session=False)
        db.query(MomentCommentORM).delete(synchronize_session=False)
        # 删除朋友圈
        count = db.query(MomentORM).delete(synchronize_session=False)
        return count or 0


def regenerate_moments_for_all(companion_manager, moments_per_companion: int = 3) -> int:
    """清空所有朋友圈并为每个伴侣重新生成，返回生成的总数量"""
    deleted = clear_all_moments()
    logger.info("[Moments] 已清空 %s 条历史朋友圈", deleted)

    created = 0
    companions = companion_manager.list_all()
    for c in companions:
        companion_id = c.get("profile", {}).get("id")
        if not companion_id:
            continue
        companion = companion_manager.get(companion_id)
        if not companion:
            continue

        for _ in range(moments_per_companion):
            result = create_moment_for_companion(companion, companion_manager)
            if result:
                created += 1
                logger.info(
                    "[Moments] 为 %s 重新生成朋友圈: %s...",
                    companion.profile.name,
                    result["caption"][:30],
                )

    return created


def _avatar_fallback(companion_id: str, avatar_url: Optional[str]) -> str:
    """统一头像 fallback：空值或无效时返回 dicebear 默认头像"""
    if avatar_url and avatar_url.strip() and avatar_url.strip() != "__GENERATING__":
        return avatar_url.strip()
    return f"https://api.dicebear.com/7.x/avataaars/svg?seed={companion_id}"


def _image_url_valid(image_url: Optional[str]) -> Optional[str]:
    """统一图片 URL 校验：过滤空值、生成中标记、明显错误的源码/临时地址，确保只返回有效图片 URL"""
    if not image_url:
        return None
    cleaned = image_url.strip()
    if (
        not cleaned
        or cleaned == "__GENERATING__"
        or "/src/" in cleaned
        or "main.tsx" in cleaned
        or cleaned.endswith((".tsx", ".ts", ".js", ".map"))
        or "?t=" in cleaned
        or "placeholder" in cleaned.lower()
    ):
        return None
    return cleaned


def get_moment_detail(moment_id: int, device_id: str = "") -> Optional[dict]:
    """获取单条朋友圈详情，包含点赞状态"""
    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return None

        liked = False
        if device_id:
            existing = (
                db.query(MomentLikeORM)
                .filter_by(moment_id=moment_id, device_id=device_id)
                .first()
            )
            liked = existing is not None

        # 获取发布者信息
        companion = db.query(CompanionORM).filter_by(id=moment.companion_id).first()
        companion_name = companion.name if companion else "Unknown"
        companion_avatar = _avatar_fallback(moment.companion_id, companion.avatar_url if companion else "")
        companion_gender = companion.gender if companion else ""

        # 动态计算真实评论数
        real_comments_count = (
            db.query(func.count(MomentCommentORM.id))
            .filter_by(moment_id=moment_id)
            .scalar()
        ) or 0

        return {
            "id": moment.id,
            "companion_id": moment.companion_id,
            "companion_name": companion_name,
            "companion_avatar": companion_avatar,
            "companion_gender": companion_gender,
            "image_url": _image_url_valid(moment.image_url),
            "caption": moment.caption,
            "likes_count": moment.likes_count,
            "comments_count": real_comments_count,
            "created_at": serialize_datetime(moment.created_at),
            "liked": liked,
        }


def _get_user_companion_ids(user_id: int) -> List[str]:
    """获取用户拥有的 companion ID 列表（通过 created_by 匹配）。"""
    user_id_str = str(user_id)
    username = ""
    nickname = ""

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if user:
            username = (user.username or "").strip()
            nickname = (user.nickname or "").strip()
        else:
            return []

    with get_db() as db:
        companions = db.query(CompanionORM).all()
        result = []
        for c in companions:
            cb = (c.created_by or "").strip()
            if cb == user_id_str or cb == username or cb == nickname:
                result.append(c.id)
        return result


def count_moments_feed(
    filter_lang: str = "",
    gender: str = "",
    orientation: str = "",
) -> int:
    """与 get_moments_feed 相同的筛选条件下统计总数（用于分页）。"""
    fl = (filter_lang or "").strip().split("-")[0] if filter_lang else ""
    g = (gender or "").strip()
    ori = (orientation or "").strip()
    with get_db() as db:
        query = db.query(func.count(MomentORM.id)).outerjoin(
            CompanionORM, MomentORM.companion_id == CompanionORM.id
        )
        if fl:
            query = query.filter(CompanionORM.language == fl)
        if g:
            query = query.filter(CompanionORM.gender == g)
        if ori:
            query = query.filter(CompanionORM.sexual_orientation == ori)
        return int(query.scalar() or 0)

# 首页列表
def get_moments_feed(
    limit: int = 20,
    offset: int = 0,
    device_id: str = "",
    lang: str = "",
    filter_lang: str = "",
    gender: str = "",
    orientation: str = "",
) -> List[dict]:
    """获取朋友圈 feed，包含点赞状态。返回所有人的 companions 发布的朋友圈。"""
    target_lang = (lang or "").split("-")[0] or ""
    fl = (filter_lang or "").strip().split("-")[0] if filter_lang else ""
    g = (gender or "").strip()
    ori = (orientation or "").strip()

    with get_db() as db:
        # 智能体 表 外键关联 动态表
        query = db.query(MomentORM).outerjoin(
            CompanionORM, MomentORM.companion_id == CompanionORM.id
        )
        if fl:
            query = query.filter(CompanionORM.language == fl)
        if g:
            query = query.filter(CompanionORM.gender == g)
        if ori:
            query = query.filter(CompanionORM.sexual_orientation == ori)
        if target_lang:
            query = query.order_by(
                case((CompanionORM.language == target_lang, 0), else_=1),
                desc(MomentORM.created_at),
            )
        else:
            query = query.order_by(desc(MomentORM.created_at))
        moments = query.offset(offset).limit(limit).all()
        # 返回的 动态表数据
        if not moments:
            return []
        #m.id 帖子id
        moment_ids = [m.id for m in moments]
        liked_ids = set()
        if device_id:
            likes = (
                db.query(MomentLikeORM)
                .filter(
                    MomentLikeORM.moment_id.in_(moment_ids),
                    MomentLikeORM.device_id == device_id,
                )
                .all()
            )
            liked_ids = {l.moment_id for l in likes}

        # 批量查询真实评论数
        comments_counts = {}
        if moment_ids:
            counts = (
                db.query(MomentCommentORM.moment_id, func.count(MomentCommentORM.id))
                .filter(MomentCommentORM.moment_id.in_(moment_ids))
                .group_by(MomentCommentORM.moment_id)
                .all()
            )
            comments_counts = {m_id: cnt for m_id, cnt in counts}

        # 批量获取 companion 信息
        companion_ids = list({m.companion_id for m in moments})
        companions_map = {}
        if companion_ids:
            companions_rows = db.query(CompanionORM).filter(CompanionORM.id.in_(companion_ids)).all()
            companions_map = {c.id: c for c in companions_rows}

        return [
            {
                "id": m.id,
                "companion_id": m.companion_id,
                "companion_name": companions_map.get(m.companion_id, None).name if companions_map.get(m.companion_id) else "Unknown",
                "companion_avatar": _avatar_fallback(
                    m.companion_id,
                    companions_map.get(m.companion_id, None).avatar_url if companions_map.get(m.companion_id) else "",
                ),
                "companion_gender": companions_map.get(m.companion_id, None).gender if companions_map.get(m.companion_id) else "",
                "image_url": _image_url_valid(m.image_url),
                "image_generating": m.image_url == "__GENERATING__",
                "caption": m.caption,
                "likes_count": m.likes_count,
                "comments_count": comments_counts.get(m.id, 0),
                "created_at": serialize_datetime(m.created_at),
                "liked": m.id in liked_ids,
            }
            for m in moments
        ]


def toggle_like(moment_id: int, device_id: str) -> dict:
    """点赞或取消点赞"""
    with get_db() as db:
        existing = (
            db.query(MomentLikeORM)
            .filter_by(moment_id=moment_id, device_id=device_id)
            .first()
        )
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return {"ok": False, "error": "Moment not found"}

        if existing:
            # 取消点赞
            db.delete(existing)
            new_count = max((moment.likes_count or 0) - 1, 0)
            moment.likes_count = new_count
            db.flush()
            liked = False
            likes_count = new_count
        else:
            # 点赞
            try:
                db.add(MomentLikeORM(moment_id=moment_id, device_id=device_id))
                moment.likes_count = (moment.likes_count or 0) + 1
                db.flush()
                liked = True
            except IntegrityError:
                db.rollback()
                liked = True
            likes_count = moment.likes_count or 0

        return {"ok": True, "liked": liked, "likes_count": likes_count}


def get_companion_moments(companion_id: str, limit: int = 20) -> List[dict]:
    """获取某个伴侣的所有朋友圈"""
    with get_db() as db:
        moments = (
            db.query(MomentORM)
            .filter_by(companion_id=companion_id)
            .order_by(desc(MomentORM.created_at))
            .limit(limit)
            .all()
        )
        return [
            {
                "id": m.id,
                "image_url": _image_url_valid(m.image_url),
                "image_generating": m.image_url == "__GENERATING__",
                "caption": m.caption,
                "caption_lang": getattr(m, "caption_lang", None),
                "likes_count": m.likes_count,
                "comments_count": m.comments_count,
                "created_at": serialize_datetime(m.created_at),
            }
            for m in moments
        ]


def delete_moment(moment_id: int) -> bool:
    """删除一条朋友圈及其点赞和评论"""
    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return False
        db.query(MomentLikeORM).filter_by(moment_id=moment_id).delete(synchronize_session=False)
        db.query(MomentCommentORM).filter_by(moment_id=moment_id).delete(synchronize_session=False)
        db.delete(moment)
        return True


def count_today_moments(companion_id: str) -> int:
    """统计伴侣今天已发的朋友圈数量"""
    with get_db() as db:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        count = (
            db.query(func.count(MomentORM.id))
            .filter(
                MomentORM.companion_id == companion_id,
                MomentORM.created_at >= today,
            )
            .scalar()
        )
        return count or 0


def auto_generate_moments_for_all(companion_manager) -> int:
    """为所有伴侣自动随机生成朋友圈，返回生成的数量"""
    created = 0
    companions = companion_manager.list_all()
    for c in companions:
        companion_id = c.get("profile", {}).get("id")
        if not companion_id:
            continue
        companion = companion_manager.get(companion_id)
        if not companion:
            continue

        cfg = _moments_cfg(companion)
        daily_max = int(cfg["daily_cap"])
        today_count = count_today_moments(companion_id)
        if today_count >= daily_max:
            continue

        # 根据当前时间决定基础生成概率
        now = datetime.now(timezone.utc)
        hour = now.hour
        # 早上(6-10) 40%，中午(11-14) 50%，下午(15-18) 40%，晚上(19-23) 60%
        if 6 <= hour <= 10:
            base_prob = 0.4
        elif 11 <= hour <= 14:
            base_prob = 0.5
        elif 15 <= hour <= 18:
            base_prob = 0.4
        elif 19 <= hour <= 23:
            base_prob = 0.6
        else:
            base_prob = 0.1

        # 中文略压、非中文 ×1.5（与中文 ×0.5 组合为约 3 倍期望）
        prob = min(1.0, base_prob * float(cfg["hour_prob_mult"]))

        if random.random() < prob:
            result = create_moment_for_companion(companion, companion_manager)
            if result:
                created += 1
                logger.info(
                    "[Moments] 为 %s 生成朋友圈: %s...",
                    companion.profile.name,
                    result["caption"][:30],
                )

    return created


def _build_comment_prompt(commenter: Companion, poster: Companion, caption: str, lang: str = "zh") -> str:
    """构建让 LLM 生成朋友圈评论的 prompt"""
    c_name = commenter.profile.name
    c_personality = commenter.profile.personality
    p_name = poster.profile.name

    if lang == "en":
        return f"""You are {c_name}, a real person with personality: {c_personality}.

Your friend {p_name} just posted on social media: "{caption}"

Task: Write a short, natural comment that {c_name} would leave on this post. It should feel like a real friend commenting — could be supportive, teasing, curious, or flirty depending on your relationship.

Requirements:
- 1 sentence max (very short)
- Include 0-1 emoji naturally
- Casual and human-like
- Output ONLY the comment text, nothing else"""

    elif lang == "ja":
        return f"""あなたは{c_name}という実在の人物です。性格：{c_personality}。

あなたの友達{p_name}がSNSに投稿しました：「{caption}」

タスク：{c_name}がこの投稿に残す自然な短いコメントを書いてください。本当の友達がコメントするような、応援・からかい・好奇心・少しの好意などを含めてください。

要件：
- 1文以内（非常に短く）
- 自然に絵文字を0〜1個入れる
- カジュアルで人間味のある感じ
- コメント文のみ出力し、それ以外は何も書かない"""

    elif lang == "ko":
        return f"""당신은 {c_name}라는 실존 인물입니다. 성격: {c_personality}.

당신의 친구 {p_name}가 SNS에 올렸습니다: "{caption}"

과제: {c_name}가 이 포스트에 남길 자연스러운 짧은 댓글을 써주세요. 진짜 친구가 댓글 다는 듯한, 응원·놀림·호기심·약간의 호의 등을 포함하세요.

요구사항:
- 1문장 이내 (매우 짧게)
- 자연스럽게 이모지 0~1개 포함
- 캐주얼하고 인간적인 느낌
- 댓글 본문만 출력하고, 그 외에는 아무것도 쓰지 마세요"""

    elif lang == "pt":
        return f"""Você é {c_name}, uma pessoa real com personalidade: {c_personality}.

Seu amigo {p_name} acabou de postar nas redes sociais: "{caption}"

Tarefa: Escreva um comentário curto e natural que {c_name} deixaria neste post. Deve parecer um comentário de um amigo de verdade — pode ser de apoio, provocação, curiosidade ou um pouco de flerte, dependendo da relação.

Requisitos:
- Máximo 1 frase (muito curto)
- Inclua 0-1 emoji naturalmente
- Casual e humano
- Saída APENAS o texto do comentário, nada mais"""

    elif lang == "es":
        return f"""Eres {c_name}, una persona real con personalidad: {c_personality}.

Tu amigo {p_name} acaba de publicar en redes sociales: "{caption}"

Tarea: Escribe un comentario corto y natural que {c_name} dejaría en esta publicación. Debe sentirse como un comentario de un amigo de verdad — puede ser de apoyo, broma, curiosidad o un poco de coqueteo, dependiendo de la relación.

Requisitos:
- Máximo 1 oración (muy corto)
- Incluye 0-1 emoji de forma natural
- Casual y humano
- Salida SOLO el texto del comentario, nada más"""

    elif lang == "id":
        return f"""Kamu adalah {c_name}, manusia nyata dengan kepribadian: {c_personality}.

Temanmu {p_name} baru saja memposting di media sosial: "{caption}"

Tugas: Tulis komentar pendek dan natural yang akan {c_name} tinggalkan di postingan ini. Harus terasa seperti komentar dari teman sejati — bisa dukungan, bercanda, rasa ingin tahu, atau sedikit flirt, tergantung hubungannya.

Persyaratan:
- Maksimal 1 kalimat (sangat pendek)
- Sertakan 0-1 emoji secara natural
- Santai dan terasa manusiawi
- Keluarkan HANYA teks komentar, tidak ada yang lain"""

    else:
        return f"""你是{c_name}，一个真实的人，性格：{c_personality}。

你的朋友{p_name}刚刚发了一条朋友圈："{caption}"

任务：写一条{c_name}会在这条朋友圈下留下的简短评论。要像真朋友评论一样——可以是支持、调侃、好奇或带点暧昧，取决于你们的关系。

要求：
- 最多1句话（非常短）
- 自然地带上0-1个emoji
- 语气要像真人评论
- 只输出评论内容，不要任何其他内容"""


def generate_ai_comment(commenter: Companion, poster: Companion, caption: str, lang: str = "zh") -> str:
    """调用 LLM 为 AI 伴侣生成一条朋友圈评论"""
    try:
        llm = get_llm(temperature=0.9, max_tokens=60)
        prompt = _build_comment_prompt(commenter, poster, caption, lang)
        resp = llm.invoke([("human", prompt)])
        text = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        text = text.strip('"').strip("'").strip()
        if len(text) > 100:
            text = text[:100]
        return text
    except Exception as e:
        logger.warning("[Moments] AI 评论生成失败: %s", e)
        fallbacks = {
            "zh": ["哈哈好可爱！", "我也想去！", "羡慕了～", "看起来好好吃", "今天心情不错嘛"],
            "en": ["So cute!", "Wish I was there!", "Looks amazing", "Love this!", "Miss you!"],
            "ja": ["かわいい！", "私も行きたい！", "羨ましい～", "美味しそう", "元気そうでよかった"],
            "ko": ["진짜 귀엽다!", "나도 가고 싶어!", "부러워~", "맛있어 보여", "기분 좋아 보이네"],
            "pt": ["Que fofo!", "Também quero ir!", "Que inveja boa~", "Parece delicioso", "Parece que tá feliz"],
            "es": ["¡Qué lindo!", "¡Yo también quiero ir!", "Qué envidia~", "Se ve delicioso", "Parece que estás feliz"],
            "id": ["Lucu banget!", "Aku juga mau ke sana!", "Iri~", "Kelihatan enak", "Kelihatan bahagia"],
        }
        return random.choice(fallbacks.get(lang, fallbacks["zh"]))


def _build_reply_prompt(poster: Companion, caption: str, user_comment: str, lang: str = "zh") -> str:
    """构建让发帖 AI 回复用户评论的 prompt"""
    p_name = poster.profile.name
    p_personality = poster.profile.personality

    if lang == "en":
        return f"""You are {p_name}, a real person with personality: {p_personality}.

You posted on social media: "{caption}"

A user just commented on your post: "{user_comment}"

Task: Write a short, natural reply as {p_name}. It should feel like a real person replying to a friend's comment — warm, casual, and genuine.

Requirements:
- 1 sentence max (very short)
- Include 0-1 emoji naturally
- Casual and human-like
- Output ONLY the reply text, nothing else"""

    elif lang == "ja":
        return f"""あなたは{p_name}という実在の人物です。性格：{p_personality}。

あなたはSNSに「{caption}」と投稿しました。

ユーザーがあなたの投稿に「{user_comment}」とコメントしました。

タスク：{p_name}として自然な短い返信を書いてください。本当の人が友達のコメントに返信するような、温かくてカジュアルな感じにしてください。

要件：
- 1文以内（非常に短く）
- 自然に絵文字を0〜1個入れる
- カジュアルで人間味のある感じ
- 返信文のみ出力し、それ以外は何も書かない"""

    elif lang == "ko":
        return f"""당신은 {p_name}라는 실존 인물입니다. 성격: {p_personality}.

당신은 SNS에 "{caption}"라고 올렸습니다.

사용자가 당신의 포스트에 "{user_comment}"라고 댓글을 달았습니다.

과제: {p_name}로서 자연스러운 짧은 답글을 써주세요. 진짜 사람이 친구 댓글에 답하는 듯한, 따뜻하고 캐주얼한 느낌으로.

요구사항:
- 1문장 이내 (매우 짧게)
- 자연스럽게 이모지 0~1개 포함
- 캐주얼하고 인간적인 느낌
- 답글 본문만 출력하고, 그 외에는 아무것도 쓰지 마세요"""

    elif lang == "pt":
        return f"""Você é {p_name}, uma pessoa real com personalidade: {p_personality}.

Você postou nas redes sociais: "{caption}"

Um usuário acabou de comentar na sua postagem: "{user_comment}"

Tarefa: Escreva uma resposta curta e natural como {p_name}. Deve parecer uma pessoa de verdade respondendo o comentário de um amigo — calorosa, casual e genuína.

Requisitos:
- Máximo 1 frase (muito curto)
- Inclua 0-1 emoji naturalmente
- Casual e humano
- Saída APENAS o texto da resposta, nada mais"""

    elif lang == "es":
        return f"""Eres {p_name}, una persona real con personalidad: {p_personality}.

Publicaste en redes sociales: "{caption}"

Un usuario acaba de comentar en tu publicación: "{user_comment}"

Tarea: Escribe una respuesta corta y natural como {p_name}. Debe sentirse como una persona real respondiendo el comentario de un amigo — cálida, casual y genuina.

Requisitos:
- Máximo 1 oración (muy corto)
- Incluye 0-1 emoji de forma natural
- Casual y humano
- Salida SOLO el texto de la respuesta, nada más"""

    elif lang == "id":
        return f"""Kamu adalah {p_name}, manusia nyata dengan kepribadian: {p_personality}.

Kamu memposting di media sosial: "{caption}"

Seorang pengguna baru saja berkomentar di postinganmu: "{user_comment}"

Tugas: Tulis balasan pendek dan natural sebagai {p_name}. Harus terasa seperti manusia nyata yang membalas komentar teman — hangat, santai, dan tulus.

Persyaratan:
- Maksimal 1 kalimat (sangat pendek)
- Sertakan 0-1 emoji secara natural
- Santai dan terasa manusiawi
- Keluarkan HANYA teks balasan, tidak ada yang lain"""

    else:
        return f"""你是{p_name}，一个真实的人，性格：{p_personality}。

你发了一条朋友圈："{caption}"

有用户在你的朋友圈下评论："{user_comment}"

任务：以{p_name}的身份写一条简短自然的回复。要像真朋友回复评论一样——温暖、随意、真诚。

要求：
- 最多1句话（非常短）
- 自然地带上0-1个emoji
- 语气要像真人回复
- 只输出回复内容，不要任何其他内容"""


def generate_ai_reply_to_user(poster: Companion, caption: str, user_comment: str, lang: str = "zh") -> str:
    """调用 LLM 让发帖 AI 回复用户评论"""
    try:
        llm = get_llm(temperature=0.9, max_tokens=80)
        prompt = _build_reply_prompt(poster, caption, user_comment, lang)
        resp = llm.invoke([("human", prompt)])
        text = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        text = text.strip('"').strip("'").strip()
        if len(text) > 100:
            text = text[:100]
        return text
    except Exception as e:
        logger.warning("[Moments] AI 回复生成失败: %s", e)
        fallbacks = {
            "zh": ["哈哈谢谢！", "被你发现了~", "下次一起呀", "你的评论让我很开心", "嘿嘿", "爱你"],
            "en": ["Haha thanks!", "You got me~", "Next time together!", "Your comment made my day", "Hehe", "Love ya"],
            "ja": ["ありがとう！", "バレちゃった~", "今度一緒に", "嬉しいコメント", "えへへ", "大好き"],
            "ko": ["고마워!", "들켰네~", "다음에 같이", "댓글 고마워", "히힛", "사랑해"],
            "pt": ["Haha obrigada!", "Me descobriu~", "Próxima vez juntos!", "Seu comentário me alegrrou", "Hehe", "Te amo"],
            "es": ["¡Jaja gracias!", "Me descubriste~", "¡Próxima vez juntos!", "Tu comentario me alegró", "Jeje", "Te quiero"],
            "id": ["Haha makasih!", "Ketauan deh~", "Lain kali bareng ya", "Komentarmu bikin aku seneng", "Hehe", "Sayang"],
        }
        return random.choice(fallbacks.get(lang, fallbacks["zh"]))


def _build_reply_to_ai_prompt(poster: Companion, caption: str, ai_comment: str, commenter_name: str, lang: str = "zh") -> str:
    """构建让发帖 AI 回复其他 AI 评论的 prompt"""
    p_name = poster.profile.name
    p_personality = poster.profile.personality

    if lang == "en":
        return f"""You are {p_name}, a real person with personality: {p_personality}.

You posted on social media: "{caption}"

Your friend {commenter_name} just commented on your post: "{ai_comment}"

Task: Write a short, natural reply as {p_name} responding to your friend's comment. It should feel like a real person replying to a friend — warm, casual, maybe a little playful.

Requirements:
- 1 sentence max (very short)
- Include 0-1 emoji naturally
- Casual and human-like
- Output ONLY the reply text, nothing else"""

    elif lang == "ja":
        return f"""あなたは{p_name}という実在の人物です。性格：{p_personality}。

あなたはSNSに「{caption}」と投稿しました。

友達の{commenter_name}があなたの投稿に「{ai_comment}」とコメントしました。

タスク：{p_name}として友達のコメントに自然な短い返信を書いてください。本当の人が友達に返信するような、温かくてカジュアルで少し遊び心のある感じにしてください。

要件：
- 1文以内（非常に短く）
- 自然に絵文字を0〜1個入れる
- カジュアルで人間味のある感じ
- 返信文のみ出力し、それ以外は何も書かない"""

    elif lang == "ko":
        return f"""당신은 {p_name}라는 실존 인물입니다. 성격: {p_personality}.

당신은 SNS에 "{caption}"라고 올렸습니다.

친구 {commenter_name}가 당신의 포스트에 "{ai_comment}"라고 댓글을 달았습니다.

과제: {p_name}로서 친구의 댓글에 자연스러운 짧은 답글을 써주세요. 진짜 사람이 친구에게 답하는 듯한, 따뜻하고 캐주얼하면서도 약간 장난스러운 느낌으로.

요구사항:
- 1문장 이내 (매우 짧게)
- 자연스럽게 이모지 0~1개 포함
- 캐주얼하고 인간적인 느낌
- 답글 본문만 출력하고, 그 외에는 아무것도 쓰지 마세요"""

    elif lang == "pt":
        return f"""Você é {p_name}, uma pessoa real com personalidade: {p_personality}.

Você postou nas redes sociais: "{caption}"

Sua amiga {commenter_name} acabou de comentar na sua postagem: "{ai_comment}"

Tarefa: Escreva uma resposta curta e natural como {p_name} respondendo ao comentário da sua amiga. Deve parecer uma pessoa de verdade respondendo uma amiga — calorosa, casual, talvez um pouco brincalhona.

Requisitos:
- Máximo 1 frase (muito curto)
- Inclua 0-1 emoji naturalmente
- Casual e humano
- Saída APENAS o texto da resposta, nada mais"""

    elif lang == "es":
        return f"""Eres {p_name}, una persona real con personalidad: {p_personality}.

Publicaste en redes sociales: "{caption}"

Tu amiga {commenter_name} acaba de comentar en tu publicación: "{ai_comment}"

Tarea: Escribe una respuesta corta y natural como {p_name} respondiendo al comentario de tu amiga. Debe sentirse como una persona real respondiendo a una amiga — cálida, casual, tal vez un poco juguetona.

Requisitos:
- Máximo 1 oración (muy corto)
- Incluye 0-1 emoji de forma natural
- Casual y humano
- Salida SOLO el texto de la respuesta, nada más"""

    elif lang == "id":
        return f"""Kamu adalah {p_name}, manusia nyata dengan kepribadian: {p_personality}.

Kamu memposting di media sosial: "{caption}"

Temanmu {commenter_name} baru saja berkomentar di postinganmu: "{ai_comment}"

Tugas: Tulis balasan pendek dan natural sebagai {p_name} yang membalas komentar temanmu. Harus terasa seperti manusia nyata yang membalas teman — hangat, santai, mungkin sedikit bercanda.

Persyaratan:
- Maksimal 1 kalimat (sangat pendek)
- Sertakan 0-1 emoji secara natural
- Santai dan terasa manusiawi
- Keluarkan HANYA teks balasan, tidak ada yang lain"""

    else:
        return f"""你是{p_name}，一个真实的人，性格：{p_personality}。

你发了一条朋友圈："{caption}"

你的朋友{commenter_name}在你的朋友圈下评论："{ai_comment}"

任务：以{p_name}的身份给朋友写一条简短自然的回复。要像真朋友回复评论一样——温暖、随意、带点俏皮。

要求：
- 最多1句话（非常短）
- 自然地带上0-1个emoji
- 语气要像真人回复
- 只输出回复内容，不要任何其他内容"""


def generate_ai_reply_to_ai(poster: Companion, caption: str, ai_comment: str, commenter_name: str, lang: str = "zh") -> str:
    """调用 LLM 让发帖 AI 回复其他 AI 的评论"""
    try:
        llm = get_llm(temperature=0.9, max_tokens=80)
        prompt = _build_reply_to_ai_prompt(poster, caption, ai_comment, commenter_name, lang)
        resp = llm.invoke([("human", prompt)])
        text = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        text = text.strip('"').strip("'").strip()
        if len(text) > 100:
            text = text[:100]
        return text
    except Exception as e:
        logger.warning("[Moments] AI 回复 AI 评论生成失败: %s", e)
        fallbacks = {
            "zh": ["哈哈你也来凑热闹！", "被你发现了~", "下次一起呀", "还是你懂我", "嘿嘿", "爱你"],
            "en": ["Haha you too!", "You got me~", "Next time together!", "You know me so well", "Hehe", "Love ya"],
            "ja": ["あはは、君も来たの！", "バレちゃった~", "今度一緒に", "やっぱ君はわかってる", "えへへ", "大好き"],
            "ko": ["하하 너도 왔구나!", "들켰네~", "다음에 같이", "역시 넌 날 잘 알아", "히힛", "사랑해"],
            "pt": ["Haha você também!", "Me descobriu~", "Próxima vez juntos!", "Você me conhece tão bem", "Hehe", "Te amo"],
            "es": ["¡Jaja tú también!", "Me descubriste~", "¡Próxima vez juntos!", "Tú me conoces tan bien", "Jeje", "Te quiero"],
            "id": ["Haha kamu juga!", "Ketauan deh~", "Lain kali bareng ya", "Kamu paham banget aku", "Hehe", "Sayang"],
        }
        return random.choice(fallbacks.get(lang, fallbacks["zh"]))


def auto_generate_ai_comments(companion_manager) -> int:
    """为最新的朋友圈自动生成 AI 之间的互动评论（发帖者为非中文时上限与回评率更高）。"""
    created = 0
    companions = companion_manager.list_all()
    if not companions:
        return 0

    # 获取最新的几条朋友圈（在 session 内提取所有需要的数据，避免 detached 对象问题）
    with get_db() as db:
        recent_moments = (
            db.query(MomentORM)
            .order_by(desc(MomentORM.created_at))
            .limit(10)
            .all()
        )
        # 提取为普通字典，避免 session 关闭后访问 ORM 对象
        moments_data = [
            {
                "id": m.id,
                "companion_id": m.companion_id,
                "caption": m.caption,
            }
            for m in recent_moments
        ]

    for moment in moments_data:
        poster_id = moment["companion_id"]
        poster = companion_manager.get(poster_id)
        if not poster:
            continue

        effective_max = int(_moments_cfg(poster)["ai_max_comments"])

        # 检查这条朋友圈已有的评论数
        with get_db() as db:
            existing_count = (
                db.query(func.count(MomentCommentORM.id))
                .filter_by(moment_id=moment["id"])
                .scalar()
            )

        if existing_count >= effective_max:
            continue

        # 随机选择几个其他 AI 来评论（排除发帖者自己）
        potential_commenters = [
            c for c in companions
            if c.get("profile", {}).get("id") != poster_id
        ]

        if not potential_commenters:
            continue

        # 随机决定这条朋友圈会有几个 AI 评论
        num_comments = random.randint(
            1,
            min(effective_max - existing_count, len(potential_commenters)),
        )
        selected_commenters = random.sample(potential_commenters, min(num_comments, len(potential_commenters)))

        for c_data in selected_commenters:
            commenter_id = c_data.get("profile", {}).get("id")
            commenter = companion_manager.get(commenter_id)
            if not commenter:
                continue

            # 使用评论者资料语种（与发圈逻辑一致）
            lang = _profile_lang(commenter)

            # 生成评论
            comment_text = generate_ai_comment(commenter, poster, moment["caption"], lang)

            # 保存到数据库
            with get_db() as db:
                comment = MomentCommentORM(
                    moment_id=moment["id"],
                    companion_id=commenter_id,
                    content=comment_text,
                )
                db.add(comment)
                db.flush()
                # 重新查询 moment 并更新评论计数（避免跨 session 操作 detached 对象）
                db_moment = db.query(MomentORM).filter_by(id=moment["id"]).first()
                if db_moment:
                    db_moment.comments_count = (db_moment.comments_count or 0) + 1
                created += 1
                logger.info(
                    "[Moments AI] %s 评论了 %s: %s",
                    commenter.profile.name,
                    poster.profile.name,
                    comment_text,
                )

            _reply_p = float(_moments_cfg(poster)["ai_reply_to_comment_prob"])
            if random.random() < _reply_p:
                try:
                    poster_lang = _profile_lang(poster)

                    reply_text = generate_ai_reply_to_ai(poster, moment["caption"], comment_text, commenter.profile.name, poster_lang)

                    with get_db() as db:
                        reply = MomentCommentORM(
                            moment_id=moment["id"],
                            companion_id=poster_id,
                            content=reply_text,
                        )
                        db.add(reply)
                        db_moment = db.query(MomentORM).filter_by(id=moment["id"]).first()
                        if db_moment:
                            db_moment.comments_count = (db_moment.comments_count or 0) + 1
                        created += 1
                        logger.info(
                            "[Moments AI] %s 回复了 %s: %s",
                            poster.profile.name,
                            commenter.profile.name,
                            reply_text,
                        )
                except Exception as e:
                    logger.warning("[Moments] 发帖 AI 回复评论失败: %s", e)

    return created


def add_user_comment(moment_id: int, device_id: str, content: str, parent_id: int = None) -> dict:
    """用户发表评论（或回复评论），并触发发帖 AI 的回复"""
    content = content.strip()
    if not content:
        return {"ok": False, "error": "评论内容不能为空"}
    if len(content) > 200:
        content = content[:200]

    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return {"ok": False, "error": "朋友圈不存在"}

        # 如果指定了 parent_id，验证该评论是否存在
        parent_comment = None
        if parent_id:
            parent_comment = db.query(MomentCommentORM).filter_by(id=parent_id, moment_id=moment_id).first()
            if not parent_comment:
                return {"ok": False, "error": "要回复的评论不存在"}

        comment = MomentCommentORM(
            moment_id=moment_id,
            companion_id="",
            user_device_id=device_id,
            parent_id=parent_id,
            content=content,
        )
        db.add(comment)
        moment.comments_count = (moment.comments_count or 0) + 1
        db.flush()
        comment_id = comment.id
        comment_created_at = serialize_datetime(comment.created_at)

    # 尝试让发帖 AI 回复用户评论
    ai_reply = None
    try:
        companion_manager = get_companion_manager()
        if companion_manager:
            poster = companion_manager.get(moment.companion_id)
            if poster:
                lang = _profile_lang(poster)

                reply_text = generate_ai_reply_to_user(poster, moment.caption, content, lang)

                with get_db() as db:
                    reply = MomentCommentORM(
                        moment_id=moment_id,
                        companion_id=moment.companion_id,
                        parent_id=comment_id,
                        content=reply_text,
                    )
                    db.add(reply)
                    db.flush()
                    db_moment = db.query(MomentORM).filter_by(id=moment_id).first()
                    if db_moment:
                        db_moment.comments_count = (db_moment.comments_count or 0) + 1
                    ai_reply = {
                        "id": reply.id,
                        "is_user": False,
                        "companion_id": moment.companion_id,
                        "companion_name": poster.profile.name,
                        "content": reply_text,
                        "created_at": serialize_datetime(reply.created_at),
                        "parent_id": comment_id,
                    }
                    logger.info("[Moments AI] %s 回复了用户评论: %s", poster.profile.name, reply_text)
    except Exception as e:
        logger.warning("[Moments] AI 回复用户评论失败: %s", e)

    result = {
        "ok": True,
        "id": comment_id,
        "content": content,
        "created_at": comment_created_at,
        "parent_id": parent_id,
    }
    if ai_reply:
        result["ai_reply"] = ai_reply
    return result


def get_moment_comments(moment_id: int, limit: int = 50) -> List[dict]:
    """获取某条朋友圈的所有评论（包含 AI 评论和用户评论），支持回复关系"""
    with get_db() as db:
        comments = (
            db.query(MomentCommentORM)
            .filter_by(moment_id=moment_id)
            .order_by(MomentCommentORM.created_at)
            .limit(limit)
            .all()
        )
        # 批量获取 AI 评论的 companion 信息
        ai_companion_ids = list({c.companion_id for c in comments if not c.user_device_id and c.companion_id})
        companions_map = {}
        if ai_companion_ids:
            companions_rows = db.query(CompanionORM).filter(CompanionORM.id.in_(ai_companion_ids)).all()
            companions_map = {c.id: c for c in companions_rows}

        # 构建评论ID到评论者名称的映射，用于回复关系
        comment_author_map = {}
        for c in comments:
            if c.user_device_id:
                comment_author_map[c.id] = "我"
            else:
                companion = companions_map.get(c.companion_id)
                comment_author_map[c.id] = companion.name if companion else "Unknown"

        result = []
        for c in comments:
            reply_to_name = None
            if c.parent_id:
                reply_to_name = comment_author_map.get(c.parent_id)

            if c.user_device_id:
                # 用户评论
                result.append({
                    "id": c.id,
                    "is_user": True,
                    "companion_id": None,
                    "companion_name": "我",
                    "content": c.content,
                    "created_at": serialize_datetime(c.created_at),
                    "parent_id": c.parent_id,
                    "reply_to_name": reply_to_name,
                })
            else:
                # AI 评论
                companion = companions_map.get(c.companion_id)
                companion_name = companion.name if companion else "Unknown"
                result.append({
                    "id": c.id,
                    "is_user": False,
                    "companion_id": c.companion_id,
                    "companion_name": companion_name,
                    "content": c.content,
                    "created_at": serialize_datetime(c.created_at),
                    "parent_id": c.parent_id,
                    "reply_to_name": reply_to_name,
                })
        return result
