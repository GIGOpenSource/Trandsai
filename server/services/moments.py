import os
import random
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import case, desc, func
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
from core.database import CompanionORM, MomentCommentORM, MomentCommentReviewORM, MomentLikeORM, MomentORM, MomentStatEventORM, SystemNotificationORM, UserORM, get_db, serialize_datetime
from core.gender import stored_gender
from core.i18n import _, auth_required_message, normalize_ui_language
from core.state import get_companion_manager
from services.agent import get_llm
from services.agent_utils import llm_content_to_str, strip_outer_markdown_fence
from services.user_facing_lang import _text_has_script, detect_user_message_lang
from services.prompts.resolve import get_pack
from services.companion_manager import Companion
from services.image_generation import generate_image_with_cache, generate_moment_image_prompt
from services.moment_image import (
    build_moment_profile,
    generate_moment_image_plan_with_avatar,
    moment_persona_block,
    profile_dict_from_companion,
)
from services.culture_data import infer_language_from_city
from services.moment_urls import image_url_valid as _image_url_valid
from services.moment_urls import moment_public_image_fields as _moment_public_image_fields
load_dotenv(override=True)
logger = logging.getLogger(__name__)

def _profile_lang(companion: Companion) -> str:
    raw = (
        getattr(companion.profile, "language", None)
        or infer_language_from_city(getattr(companion.profile, "city", "") or "")
        or "zh"
    )
    return normalize_ui_language((raw or "zh").split("-")[0].strip() or "zh")


def _resolve_moment_interact_lang(text: Optional[str], fallback: str = "en") -> str:
    """有可识别正文则跟正文语言；无内容/纯表情则用 fallback。"""
    fb = normalize_ui_language((fallback or "en").split("-")[0].strip() or "en")
    t = (text or "").strip()
    if not t or not _text_has_script(t):
        return fb
    return detect_user_message_lang(t, fb)


def _ai_comment_lang(
    caption: Optional[str],
    commenter: Companion,
    *,
    caption_lang: Optional[str] = None,
    poster: Optional[Companion] = None,
) -> str:
    """自动评论用语：跟帖子正文语言；无正文则评论者人设语言。"""
    cap = (caption or "").strip()
    if cap and _text_has_script(cap):
        stored = normalize_ui_language((caption_lang or "").split("-")[0].strip()) if caption_lang else ""
        fb = stored or ( _profile_lang(poster) if poster else _profile_lang(commenter) )
        return _resolve_moment_interact_lang(cap, fb)
    return _profile_lang(commenter)


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




def _build_moment_prompt(companion: Companion, lang: str = "zh") -> str:
    """构建让 LLM 生成朋友圈文案的 prompt，带随机长度和多语言本地化吸引力逻辑"""
    profile = companion.profile
    name = profile.name
    personality = profile.personality
    background = profile.background
    city = profile.city
    mood = companion.state.mood
    affection = companion.state.affection
    persona_ctx = moment_persona_block(companion)

    # 随机长度策略：让文案长短自然变化，模拟真人发社交动态的习惯
    length_roll = random.random()
    if length_roll < 0.15:
        # 15% 短随手感（降低超短占比，减少「一句+表情」模板感）
        length_zh = "这次偏短：可以1句话，但要有具体画面（地点/物件/气味），不要只有感叹词+emoji"
        length_en = "A bit short: 1 sentence ok, but must include a concrete detail (place/object/sense). Avoid emoji-only vibe."
        length_ja = "短めで：1文でもいいが、具体的な場面（場所・物・匂い）を必ず。絵文字だけは禁止。"
        length_ko = "짧게 가도 됨: 1문장도 OK지만 구체적 장면(장소/물건) 필수. 이모지만으로 끝내기 금지."
        length_pt = "Curto ok: 1 frase, mas com detalhe concreto. Evite só emoji."
        length_es = "Corto ok: 1 oración con detalle concreto. Evita solo emoji."
        length_id = "Pendek ok: 1 kalimat dengan detail konkret. Hindari hanya emoji."
    elif length_roll < 0.55:
        # 40% 中等
        length_zh = "这次写2-3句话，40-90个字：有细节、有情绪转折，像真人朋友圈，不要鸡汤也不要机关枪emoji"
        length_en = "Write 2-3 sentences, 25-55 words: concrete detail + emotional turn. Human feed post, not motivational quote, emoji sparingly (0-1)."
        length_ja = "2〜3文、40〜90字。具体と感情の揺らぎ。絵文字は0〜1個。"
        length_ko = "2~3문장, 40~90자. 구체적 디테일+감정. 이모지 0~1개."
        length_pt = "2-3 frases, 40-90 caracteres equivalentes: detalhe + emoção. 0-1 emoji."
        length_es = "2-3 oraciones: detalle + emoción. 0-1 emoji."
        length_id = "2-3 kalimat: detail + emosi. 0-1 emoji."
    else:
        # 45% 稍长叙事
        length_zh = "这次稍长：2-4句话，60-120个字，讲一点小事的前因后果或吐槽，像真人在发状态"
        length_en = "Longer: 2-4 sentences with a tiny anecdote. Sound like a real person updating friends."
        length_ja = "やや長め：小さな出来事の経緯やぼやき。リアルな人が投稿する感じ。"
        length_ko = "조금 길게: 작은 일화나 투덜거림. 진짜 사람 피드처럼."
        length_pt = "Mais longo: minianedota realista."
        length_es = "Más largo: mini anécdota realista."
        length_id = "Lebih panjang: anekdot kecil yang realistis."

    if lang == "en":
        return f"""You are {name}, a real person living in {city}.

Your personality: {personality}
Your background: {background}
Current mood: {mood}
Relationship closeness: {affection}/100

Locale & social feed style (from companions profile — MUST follow):
{persona_ctx}

Task: Write a short social media post (like Instagram, Twitter/X, or a story) that {name} would naturally share. Use "attraction psychology" — make people feel like you're living an interesting, high-value life, and create a "hook" that makes them want to reply, comment, or DM you.

Attraction Rules (follow these):
1. Curiosity Gap: mention something intriguing but don't fully explain it — leave them wanting to ask
2. High-Value Lifestyle: subtly show you're living well, NOT bragging
3. Implication over Directness: make them feel "I wish I was there" instead of saying "I miss you"
4. Emotional Resonance: the post should carry a vibe (joy, surprise, chill, anticipation)
5. Social Proof Lite: casually hint that others notice/appreciate you, but don't force it

{length_en}
- Emoji optional: at most 1, skip if it feels forced
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

地域・SNS文体（companions 設定 — 必ず守る）：
{persona_ctx}

タスク：InstagramやLINEのタイムライン、あるいはX（旧Twitter）のように、{name}が自然に投稿するような短文を書いてください。「吸引力の法則」を使って——見た人が「この人魅力的だな」「もっと知りたい」と思わずコメントやDMしたくなるような「フック」を必ず含めてください。

吸引力ロジック（必ず守って）：
1. 「情報の欠口」を作る：面白いことをほのめかすが、全部は言わない。聞きたくなるように。
2. 高価値なライフスタイルを自然に：自慢ではなく、「私は充実してる」という雰囲気を漂わせる
3. 暗示で直球を避ける：「会いたい」「好き」ではなく、「一緒にいたかったな」という気持ちを読者に思わせる
4. 感情の共感力：喜び、小さな驚き、ゆるさ、期待感——感情を込める
5. ソーシャルプルーフ（軽く）：他の人からの注目や評価をさりげなく示すが、無理はしない

{length_ja}
- 絵文字は任意：最大1個。無理なら入れない
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

지역·SNS 문체 (companions 설정 — 반드시 준수):
{persona_ctx}

과제: 인스타그램이나 카카오스토리처럼, {name}가 자연스럽게 올릴 만한 짧은 글을 써주세요. "매력 법칙"을 사용해서——보는 사람이 "이 사람 매력적이다" "더 알고 싶다"고 느끼게 하고, 댓글이나 DM을 보내고 싶어지는 "훅"을 반드시 넣어주세요.

매력 로직(반드시 따를 것):
1. "호기심의 틈" 만들기: 흥미로운 걸 암시하지만 다 말하지는 않기. 묻고 싶게 만들기.
2. 높은 가치의 라이프스타일 자연스럽게: 자랑이 아니라 "나는 잘 지내고 있어"라는 분위기 풍기기
3. 직접 표현 대신 암시: "보고 싶어" "좋아해" 대신, "함께 있었으면 좋겠다"는 생각이 들게 하기
4. 감정의 공감력: 기쁨, 작은 놀라움, 여유, 기대감——감정을 담기
5. 소셜 프루프(가볍게): 다른 사람들의 관심이나 인정을 살짝 보여주되, 억지는 내지 않기

{length_ko}
- 이모지 선택: 최대 1개, 억지면 생략
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

Local e estilo de feed (perfil companions — OBRIGATÓRIO):
{persona_ctx}

Tarefa: Escreva uma postagem curta de mídia social (tipo Instagram, WhatsApp Status ou story) que {name} compartilharia naturalmente. Use a "psicologia da atração" — faça as pessoas sentirem que você leva uma vida interessante e de alto valor, e crie um "gancho" que as faça querer responder, comentar ou mandar mensagem.

Regras de Atração (siga estas):
1. Lacuna de Curiosidade: mencione algo intrigante mas não explique completamente — deixe-as querendo perguntar
2. Estilo de Vida de Alto Valor: mostre sutilmente que vive bem, NÃO seja arrogante
3. Implicação em vez de Diretude: faça-as sentir "queria estar aí" em vez de dizer "sinto sua falta"
4. Ressonância Emocional: a postagem deve carregar uma vibe (alegria, surpresa, descontração, expectativa)
5. Prova Social Leve: dê uma dica casual de que outros notam/apreciam você, mas sem forçar

{length_pt}
- Emoji opcional: no máximo 1
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

Local y estilo de feed (perfil companions — OBLIGATORIO):
{persona_ctx}

Tarea: Escribe una publicación corta en redes sociales (como Instagram, una historia o Twitter/X) que {name} compartiría naturalmente. Usa la "psicología de la atracción" — haz que la gente sienta que llevas una vida interesante y de alto valor, y crea un "gancho" que los haga querer responder, comentar o enviarte un mensaje.

Reglas de Atracción (sigue estas):
1. Brecha de Curiosidad: menciona algo intrigante pero no lo expliques del todo — déjalos con ganas de preguntar
2. Estilo de Vida de Alto Valor: muestra sutilmente que vives bien, NO presumiendo
3. Implicación en vez de Directitud: haz que sientan "ojalá estuviera ahí" en lugar de decir "te extraño"
4. Resonancia Emocional: la publicación debe llevar una vibra (alegría, sorpresa, tranquilidad, expectativa)
5. Prueba Social Ligera: insinúa casualmente que otros te notan/aprecian, pero sin forzarlo

{length_es}
- Emoji opcional: máximo 1
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

Lokal & gaya feed (profil companions — WAJIB):
{persona_ctx}

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

国家/地区与发文风格（来自 companions 表配置，必须遵守）：
{persona_ctx}

任务：写一条像微信朋友圈/小红书/Instagram那样，{name}会自然发出的动态。要用"吸引力法则"来设计——让看到的人觉得这个人很有魅力、生活很精彩，忍不住想点赞、评论或私聊。文案必须带有一个"钩子"（悬念、好奇缺口、或暗示），让人产生"我想了解更多"的冲动。

吸引力逻辑（必须遵循）：
1. 制造"好奇缺口"：提到一个有趣的东西但不说完，或说一半留一半，让人忍不住想问
2. 展示高价值生活状态：不是炫耀，而是自然流露"我过得充实且有趣"
3. 用暗示代替直白：不要直接表达好感，而是让对方感觉"如果我也在场就好了"
4. 情绪感染力：让文案带有一种情绪（愉悦、小惊喜、慵懒、期待），让人想共鸣
5. 社交认证感：可以偶尔带一点"被他人认可/关注"的轻描淡写，但不要刻意

{length_zh}
- emoji 可选：最多1个，不硬塞；禁止句末堆叠表情
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
        max_token = int(os.getenv("MAX_TOKENS", 1024))
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
        candidates = fallbacks.get(lang, fallbacks["en"])
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
    profile_dict = profile_dict_from_companion(companion)
    avatar_url = getattr(companion.profile, "avatar_url", "") or ""
    img_prompt, img_style, _used_vision = generate_moment_image_plan_with_avatar(
        caption,
        profile_dict,
        avatar_url,
        lang=effective_lang,
    )
    try:
        image_url = generate_image_with_cache(img_prompt, style=img_style, width=600, height=600)
    except Exception as e:
        logger.warning("moment image generation failed companion=%s: %s", companion.profile.id, e)
        image_url = ""

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
        _add_ai_comments_to_moment(
            moment_id, companion, caption, companion_manager, caption_lang=effective_lang
        )

    return result


def _add_ai_comments_to_moment(
    moment_id: int,
    poster: Companion,
    caption: str,
    companion_manager,
    max_comments: Optional[int] = None,
    caption_lang: Optional[str] = None,
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

        # 有正文跟帖子语言；无内容则评论者人设语言
        lang = _ai_comment_lang(
            caption, commenter, caption_lang=caption_lang, poster=poster
        )

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
                # 回复跟被评那条的语言
                reply_lang = _resolve_moment_interact_lang(comment_text, lang)

                reply_text = generate_ai_reply_to_ai(
                    poster, caption, comment_text, commenter.profile.name, reply_lang
                )

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
    """根据朋友圈文案 AND 机器人个人资料生成配图，返回图片 URL。
    已有有效配图则直接返回，不重新生成；空图 / 生成失败占位才真正生图。"""
    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return None

        existing = _image_url_valid(moment.image_url)
        if existing:
            return existing

        # 没有文案则无法生成
        if not moment.caption:
            return None

        # 加载伴侣资料以个性化prompt
        companion = db.query(CompanionORM).filter_by(id=moment.companion_id).first()
        profile_dict = build_moment_profile(companion) if companion else None
        avatar_url = (profile_dict or {}).get("avatar_url", "")

        img_prompt, img_style, _ = generate_moment_image_plan_with_avatar(
            moment.caption,
            profile_dict,
            avatar_url,
            lang=(profile_dict or {}).get("language", "zh"),
        )
        # 失败向上抛 ImageGenerationError，由 admin API 转 502
        new_url = generate_image_with_cache(img_prompt, style=img_style, width=600, height=600)
        moment.image_url = new_url
        db.commit()  # 确保更新保存
        return new_url


def _icebreaker_prompts(moment_caption: str, lang: str = "zh") -> tuple:
    """破冰文案：返回 (system, user)。system 只管规矩，user 只给朋友圈+任务。"""
    pack = get_pack(lang)
    caption = (moment_caption or "").strip().replace("\n", " ")
    if len(caption) > 80:
        caption = caption[:80] + "…"
    return pack["icebreaker_system"], pack["icebreaker_user"].format(caption=caption)


_OPTION_JUNK_RE = re.compile(
    r"(?i)\boption\s*[a-d]\b|选项\s*[abc一二三四]|choice\s*[a-d]|方案\s*[abc]|候选"
)


def _parse_icebreaker_response(raw: str, lang: str = "zh") -> Optional[str]:
    """提取破冰文案：去掉明显脏格式，保留模型正常口语句。"""
    if not raw:
        return None
    text = strip_outer_markdown_fence(llm_content_to_str(raw)).strip()
    if not text:
        return None

    # 明显多候选 / 列表 → 不可用
    if _OPTION_JUNK_RE.search(text):
        return None
    if re.search(r"(?m)^\s*([*\-•]|\d+[\.\)])\s+\S", text) and text.count("\n") >= 1:
        return None

    parts = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not parts:
        return None
    text = " ".join(parts[:2]).strip().strip('"').strip("'").strip()
    text = text.strip("*").strip("`").strip()

    for prefix in ("icebreaker:", "破冰文案:", "文案:", "response:", "output:", "message:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip().strip('"').strip("'")

    if not text or "\ufffd" in text:
        return None
    if len(text) < 2 or len(text) > 120:
        return None
    # 仅拦截「逗号/顿号收尾」的半句
    if re.search(r"[，、；,]\s*$", text):
        return None
    return text


def generate_icebreaker(moment_id: int, user_id: Optional[int] = None, lang: str = "zh") -> Optional[dict]:
    """根据朋友圈内容生成破冰文案（每个用户×伴侣最多一次）。

    Returns:
        - None: 朋友圈不存在（接口层 404）
        - {"is_first": False, "icebreaker": None}: 已有任意聊天记录（含 moment），不再生成
        - {"is_first": True, "icebreaker": str|None}: 首次生成；失败则为 null（不 404）
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from core.database import ShortTermMessageORM

    lang = normalize_ui_language(lang)

    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return None

        caption = (moment.caption or "").strip()
        companion_id = moment.companion_id

        companion = db.query(CompanionORM).filter_by(id=companion_id).first()
        if companion and companion.is_deleted:
            return {"is_first": False, "icebreaker": None, "companion_deleted": True}
        if user_id is not None and companion_id:
            from services.companion_report import assert_companion_visible_to_user

            if assert_companion_visible_to_user(int(user_id), companion_id):
                return {"is_first": False, "icebreaker": None, "companion_hidden": True}

        # 有任意会话记录（moment / user / assistant / …）→ 非首次，不生成
        if user_id is not None:
            already = (
                db.query(ShortTermMessageORM.id)
                .filter(
                    ShortTermMessageORM.user_id == user_id,
                    ShortTermMessageORM.companion_id == companion_id,
                )
                .first()
            )
            if already:
                return {"is_first": False, "icebreaker": None}

    if user_id is not None and companion_id:
        try:
            from services.outreach import cancel_pending_greeting_outreach

            cancel_pending_greeting_outreach(user_id=user_id, companion_id=companion_id)
        except Exception as e:
            logger.warning("[Moments] 取消打招呼触达失败: %s", e)

    if not caption:
        return {"is_first": True, "icebreaker": None}

    text = None
    try:
        max_token = min(int(os.getenv("ICEBREAKER_MAX_TOKENS", "512") or 512), 512)
        llm = get_llm(temperature=0.5, max_tokens=max_token, role="icebreaker")
        system, user_prompt = _icebreaker_prompts(caption, lang)

        for attempt in range(3):
            resp = llm.invoke(
                [SystemMessage(content=system), HumanMessage(content=user_prompt)]
            )
            raw_content = llm_content_to_str(getattr(resp, "content", ""))
            parsed = _parse_icebreaker_response(raw_content, lang=lang)
            if parsed:
                text = parsed
                break
            logger.warning(
                "[Moments] 破冰文案质量不合格 attempt=%s raw=%s",
                attempt + 1,
                (raw_content or "")[:200],
            )
    except Exception as e:
        logger.warning("[Moments] 破冰文案生成失败: %s", e)

    return {"is_first": True, "icebreaker": text}


_SAY_HI_FALLBACK = {
    "zh": "你好，想和你聊聊。",
    "en": "Hey, would love to chat.",
    "ja": "こんにちは。お話ししませんか。",
    "ko": "안녕하세요. 이야기하고 싶어요.",
}


def _say_hi_fallback(lang: str) -> str:
    lang = normalize_ui_language(lang) or "en"
    return _SAY_HI_FALLBACK.get(lang) or _SAY_HI_FALLBACK["en"]


def pick_say_hi_message(lang: str) -> str:
    """从文案库按语种随机抽一条；库空则硬编码兜底。"""
    try:
        from services.say_hi_copy import pick_say_hi_content

        with get_db() as db:
            return pick_say_hi_content(db, lang)
    except Exception as e:
        logger.warning("[Moments] say_hi 抽文案失败，用兜底: %s", e)
        return _say_hi_fallback(lang)


def _user_has_said_hi(db, user_id: int, companion_id: str) -> bool:
    """是否已向该智能体打过招呼（发过 user 消息）。仅 moment 卡片不算。"""
    from core.database import ShortTermMessageORM

    if not user_id or not companion_id:
        return False
    row = (
        db.query(ShortTermMessageORM.id)
        .filter(
            ShortTermMessageORM.user_id == user_id,
            ShortTermMessageORM.companion_id == companion_id,
            ShortTermMessageORM.role == "user",
        )
        .first()
    )
    if row:
        return True
    try:
        from core.chat_cache import bridge_enabled, get_recent

        if bridge_enabled():
            recent = get_recent(companion_id, n=20, user_id=user_id) or []
            for m in recent:
                if (m.get("role") or "") == "user" and (m.get("content") or "").strip():
                    return True
    except Exception:
        pass
    return False


def batch_companions_said_hi(user_id: Optional[int], companion_ids: List[str]) -> set:
    """批量：哪些 companion 当前用户已打过招呼（发过 user 消息）。"""
    ids = [str(c).strip() for c in (companion_ids or []) if c]
    if not user_id or not ids:
        return set()
    said: set = set()
    with get_db() as db:
        from core.database import ShortTermMessageORM as _STM

        rows = (
            db.query(_STM.companion_id)
            .filter(
                _STM.user_id == int(user_id),
                _STM.companion_id.in_(ids),
                _STM.role == "user",
            )
            .distinct()
            .all()
        )
        said = {str(r[0]) for r in rows if r[0]}
    pending = [cid for cid in ids if cid not in said]
    if pending:
        try:
            from core.chat_cache import bridge_enabled, get_recent

            if bridge_enabled():
                for cid in pending:
                    try:
                        recent = get_recent(cid, n=10, user_id=int(user_id)) or []
                        if any(
                            (m.get("role") or "") == "user" and (m.get("content") or "").strip()
                            for m in recent
                        ):
                            said.add(cid)
                    except Exception:
                        continue
        except Exception:
            pass
    return said


def attach_is_say_hi_to_companions(items: List[dict], user_id: Optional[int]) -> List[dict]:
    """给智能体列表项挂 is_say_hi（读 profile.id）。"""
    if not items:
        return items
    cids = []
    for it in items:
        cid = ((it.get("profile") or {}).get("id") or it.get("id") or "").strip()
        if cid:
            cids.append(cid)
    said = batch_companions_said_hi(user_id, cids)
    exhausted = False
    if user_id:
        from services.say_hi_quota import get_say_hi_daily_snapshot

        exhausted = bool(get_say_hi_daily_snapshot(int(user_id)).get("say_hi_daily_exhausted"))
    for it in items:
        cid = ((it.get("profile") or {}).get("id") or it.get("id") or "").strip()
        it["is_say_hi"] = bool(cid and cid in said)
        it["say_hi_daily_exhausted"] = exhausted
    return items


def attach_is_say_hi_to_moments(items: List[dict], user_id: Optional[int]) -> List[dict]:
    """给朋友圈列表项挂 is_say_hi（按 companion_id）。"""
    if not items:
        return items
    cids = [str(it.get("companion_id") or "").strip() for it in items if it.get("companion_id")]
    said = batch_companions_said_hi(user_id, cids)
    exhausted = False
    if user_id:
        from services.say_hi_quota import get_say_hi_daily_snapshot

        exhausted = bool(get_say_hi_daily_snapshot(int(user_id)).get("say_hi_daily_exhausted"))
    for it in items:
        cid = str(it.get("companion_id") or "").strip()
        it["is_say_hi"] = bool(cid and cid in said)
        it["say_hi_daily_exhausted"] = exhausted
    return items


def say_hi_to_companion(
    companion_id: str,
    user_id: int,
    lang: str = "zh",
    *,
    use_chat_free: bool = False,
) -> Optional[dict]:
    """按智能体打招呼：后台文案 + 语种随机抽一条，写成 user 消息。

    不依赖朋友圈。是否已打过招呼只看该用户对该智能体是否已有 user 消息。
    扣费：只扣每日 Say Hi 免费；日额用尽不自动扣聊天免费。
    用户确认继续（use_chat_free=True）后，才扣每智能体聊天免费 / 积分。
    失败返回 charge_error 或 need_confirm_use_chat_free，不写消息。
    """
    from services.memory import ShortTermMemory

    cid = (companion_id or "").strip()[:8]
    lang = normalize_ui_language(lang)
    if not cid or not user_id:
        return None

    with get_db() as db:
        companion = db.query(CompanionORM).filter_by(id=cid).first()
        if not companion:
            return None
        if companion.is_deleted:
            return {
                "is_say_hi": False,
                "message": None,
                "companion_id": cid,
                "already": False,
                "companion_deleted": True,
            }
        from services.companion_report import assert_companion_visible_to_user

        if assert_companion_visible_to_user(int(user_id), cid):
            return {
                "is_say_hi": False,
                "message": None,
                "companion_id": cid,
                "already": False,
                "companion_hidden": True,
            }
        if _user_has_said_hi(db, user_id, cid):
            return {
                "is_say_hi": True,
                "message": None,
                "companion_id": cid,
                "already": True,
            }

    from services.say_hi_quota import decide_say_hi_charge

    billed = decide_say_hi_charge(int(user_id), cid, use_chat_free=bool(use_chat_free))
    if billed.get("need_confirm"):
        return {
            "is_say_hi": False,
            "message": None,
            "companion_id": cid,
            "already": False,
            "say_hi_daily_exhausted": True,
            "need_confirm_use_chat_free": True,
            "charge_meta": billed.get("charge_meta"),
        }
    if billed.get("charge_error"):
        return {
            "is_say_hi": False,
            "message": None,
            "companion_id": cid,
            "already": False,
            "say_hi_daily_exhausted": True,
            "charge_error": billed["charge_error"],
        }
    if not billed.get("ok"):
        return {
            "is_say_hi": False,
            "message": None,
            "companion_id": cid,
            "already": False,
            "say_hi_daily_exhausted": True,
        }
    charge_meta = billed.get("charge_meta") or {}

    try:
        from services.outreach import cancel_pending_greeting_outreach

        cancel_pending_greeting_outreach(user_id=user_id, companion_id=cid)
    except Exception as e:
        logger.warning("[Moments] say_hi 取消打招呼触达失败: %s", e)

    text = pick_say_hi_message(lang)
    try:
        ShortTermMemory(cid, user_id=user_id).add("user", text)
    except Exception:
        from services.chat_turn import refund_chat_turn
        from services.say_hi_quota import restore_say_hi_daily_free

        if charge_meta.get("use_say_hi_daily_free"):
            restore_say_hi_daily_free(int(user_id))
        else:
            refund_chat_turn(int(user_id), charge_meta)
        raise
    return {
        "is_say_hi": True,
        "message": text,
        "companion_id": cid,
        "already": False,
        "charge_meta": charge_meta,
    }


def say_hi(
    moment_id: int,
    user_id: int,
    lang: str = "zh",
    *,
    use_chat_free: bool = False,
) -> Optional[dict]:
    """兼容旧路径：用朋友圈找到 companion_id，再走按智能体打招呼。"""
    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return None
        companion_id = moment.companion_id
    if not companion_id:
        return None
    return say_hi_to_companion(
        companion_id, user_id=user_id, lang=lang, use_chat_free=use_chat_free
    )


def save_moment_to_history(moment_id: int, user_id: int) -> bool:
    """将朋友圈详情保存到历史聊天记录

    Args:
        moment_id: 朋友圈ID
        user_id: 用户ID

    Returns:
        是否保存成功
    """
    import json
    from core.database import ShortTermMessageORM, MomentLikeORM

    try:
        with get_db() as db:
            # 1. 查询朋友圈
            moment = db.query(MomentORM).filter_by(id=moment_id).first()
            if not moment:
                return False

            # 2. 查询用户是否点赞了该朋友圈
            liked = False
            if user_id:
                like_record = db.query(MomentLikeORM).filter(
                    MomentLikeORM.moment_id == moment_id,
                    MomentLikeORM.user_id == user_id
                ).first()
                liked = like_record is not None

            # 3. 构建朋友圈详情 JSON
            moment_data = {
                "moment_id": moment_id,
                "caption": moment.caption,
                **_moment_public_image_fields(moment),
                "liked": liked,
            }

            # 4. 查找是否已存在该 moment 的记录，存在则更新，不存在则新增
            existing_message = db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == moment.companion_id,
                ShortTermMessageORM.user_id == user_id,
                ShortTermMessageORM.role == "moment"
            ).first()

            if existing_message:
                # 更新已有的 moment 记录（点赞状态、图片等）
                existing_message.content = json.dumps(moment_data, ensure_ascii=False)
            else:
                # 新增 moment 记录
                moment_message = ShortTermMessageORM(
                    companion_id=moment.companion_id,
                    user_id=user_id,
                    role="moment",
                    content=json.dumps(moment_data, ensure_ascii=False),
                )
                db.add(moment_message)

            return True
    except Exception as e:
        logger.warning("[Moments] 保存朋友圈详情到历史记录失败: %s", e)
        return False


def _moment_message_matches_id(content: Optional[str], moment_id: int) -> bool:
    """判断 role=moment 消息 content 是否对应指定 moment_id。"""
    import json

    if not content:
        return False
    try:
        data = json.loads(content)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    try:
        return int(data.get("moment_id")) == int(moment_id)
    except (TypeError, ValueError):
        return False


def save_moment_card_once(moment_id: int, user_id: int) -> Optional[dict]:
    """按朋友圈维度写入聊天记录：同一 moment_id 只写一次，不同动态互不影响。

    与 save_moment_to_history（破冰/say_hi：用户×伴侣仅保留一条 moment）不同，
    本函数按 moment_id 去重，可对同一伴侣追加多条 moment 卡片。

    Returns:
        None: 朋友圈不存在
        dict: saved / already / companion_id / moment
    """
    import json
    from core.database import ShortTermMessageORM, MomentLikeORM

    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return None

        companion_id = moment.companion_id
        liked = (
            db.query(MomentLikeORM.id)
            .filter(
                MomentLikeORM.moment_id == moment_id,
                MomentLikeORM.user_id == user_id,
            )
            .first()
            is not None
        )
        moment_card = {
            "moment_id": moment_id,
            "caption": moment.caption,
            **_moment_public_image_fields(moment),
            "liked": liked,
        }

        existing_rows = (
            db.query(ShortTermMessageORM)
            .filter(
                ShortTermMessageORM.companion_id == companion_id,
                ShortTermMessageORM.user_id == user_id,
                ShortTermMessageORM.role == "moment",
            )
            .order_by(ShortTermMessageORM.created_at.asc())
            .all()
        )
        for row in existing_rows:
            if _moment_message_matches_id(row.content, moment_id):
                return {
                    "saved": False,
                    "already": True,
                    "companion_id": companion_id,
                    "moment": moment_card,
                }

        db.add(
            ShortTermMessageORM(
                companion_id=companion_id,
                user_id=user_id,
                role="moment",
                content=json.dumps(moment_card, ensure_ascii=False),
            )
        )
        return {
            "saved": True,
            "already": False,
            "companion_id": companion_id,
            "moment": moment_card,
        }


def confirm_moment_im(moment_id: int, user_id: int) -> Optional[dict]:
    """评论达上限后确认进入私聊：按朋友圈维度写入聊天卡片（可多次，按 moment 去重）。

    业务主体是朋友圈（非智能体）：
    - 不同动态可分别写入，互不影响
    - 同一动态已写入过则跳过（already=true）
    - 不生成破冰文案

    Returns:
        - None: 朋友圈不存在
        - dict: moment_saved / already / companion_id / moment / companion_deleted|hidden
    """
    from core.database import ShortTermMessageORM

    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return None

        companion_id = moment.companion_id

        companion = db.query(CompanionORM).filter_by(id=companion_id).first()
        if companion and companion.is_deleted:
            return {
                "moment_saved": False,
                "already": False,
                "companion_id": companion_id,
                "moment_id": moment_id,
                "companion_deleted": True,
            }

        from services.companion_report import assert_companion_visible_to_user

        if assert_companion_visible_to_user(int(user_id), companion_id):
            return {
                "moment_saved": False,
                "already": False,
                "companion_id": companion_id,
                "moment_id": moment_id,
                "companion_hidden": True,
            }

        had_any_chat = (
            db.query(ShortTermMessageORM.id)
            .filter(
                ShortTermMessageORM.user_id == user_id,
                ShortTermMessageORM.companion_id == companion_id,
            )
            .first()
            is not None
        )

    if not had_any_chat:
        try:
            from services.outreach import cancel_pending_greeting_outreach

            cancel_pending_greeting_outreach(user_id=user_id, companion_id=companion_id)
        except Exception as e:
            logger.warning("[Moments] confirm-im 取消打招呼触达失败: %s", e)

    write = save_moment_card_once(moment_id, user_id)
    if write is None:
        return None

    return {
        "moment_saved": bool(write.get("saved")),
        "already": bool(write.get("already")),
        "companion_id": write.get("companion_id") or companion_id,
        "moment_id": moment_id,
        "moment": write.get("moment"),
    }


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


def get_moment_detail(moment_id: int, user_id: Optional[int] = None) -> Optional[dict]:
    """获取单条朋友圈详情，包含点赞状态"""
    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return None
        from services.moment_schedule import is_moment_published

        if not is_moment_published(moment):
            return None

        # 检查发布者是否已删除（创建者侧隐藏；其他人仍可见）
        companion = db.query(CompanionORM).filter_by(id=moment.companion_id).first()
        if not companion:
            return None
        owner_keys = _owner_keys_for_user(user_id, db=db)
        if companion.is_deleted and (
            owner_keys is None or _viewer_owns_companion_row(companion, owner_keys)
        ):
            return None
        if not getattr(companion, "is_active", True):
            return None
        if user_id:
            from services.companion_report import assert_companion_visible_to_user

            if assert_companion_visible_to_user(int(user_id), moment.companion_id):
                return None

        liked = False
        # 只使用 user_id 判断点赞状态
        if user_id:
            existing = (
                db.query(MomentLikeORM)
                .filter_by(moment_id=moment_id, user_id=user_id)
                .first()
            )
            liked = existing is not None

        # 获取发布者信息（复用上面已查询的 companion）
        companion_name = companion.name if companion else _("已注销")
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
            **_moment_public_image_fields(moment),
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


def _owner_keys_for_user(user_id: Optional[int], db=None) -> Optional[set]:
    """创建者匹配键；无 user_id 时返回 None（表示对所有人隐藏已软删）。"""
    if user_id is None:
        return None
    keys = {str(user_id)}

    def _fill(session):
        user = session.query(UserORM).filter(UserORM.id == user_id).first()
        if user:
            if (user.username or "").strip():
                keys.add(user.username.strip())
            if (user.nickname or "").strip():
                keys.add(user.nickname.strip())

    if db is not None:
        _fill(db)
    else:
        with get_db() as session:
            _fill(session)
    return keys


def _soft_delete_visibility_clause(owner_keys: Optional[set]):
    """未删除全员可见；已删除仅非创建者可见。"""
    from sqlalchemy import or_

    from services.companion_manager import CompanionManager

    return CompanionManager.soft_delete_visibility_clause(CompanionORM, owner_keys)


def _viewer_owns_companion_row(companion: CompanionORM, owner_keys: Optional[set]) -> bool:
    if not companion or not owner_keys:
        return False
    return (companion.created_by or "").strip() in owner_keys


def _excluded_companion_ids(db, user_id: Optional[int]) -> set:
    from services.companion_report import list_excluded_companion_ids_for_user

    if not user_id:
        return globally_inactive_only(db)
    return list_excluded_companion_ids_for_user(db, int(user_id))


def globally_inactive_only(db) -> set:
    from services.companion_report import globally_inactive_companion_ids

    return globally_inactive_companion_ids(db)


def _moments_visible_query(db, user_id: Optional[int] = None):
    from services.moment_schedule import apply_published_moment_filter

    query = db.query(MomentORM).outerjoin(
        CompanionORM, MomentORM.companion_id == CompanionORM.id
    ).filter(
        CompanionORM.is_deleted.isnot(True),
        CompanionORM.is_active.isnot(False),
    )
    excluded = _excluded_companion_ids(db, user_id)
    if excluded:
        query = query.filter(~CompanionORM.id.in_(excluded))
    query = apply_published_moment_filter(query)
    return query


def resolve_moments_pool(db, user_id: Optional[int], ui_lang: str, recommend_debug=None):
    """feed 与 count 共用：瀑布定池（不含硬筛）。"""
    from services.recommend import (
        apply_agent_tag_filters,
        apply_hard_filters,
        apply_waterfall_to_companion_query,
        resolve_companion_list_lang,
        load_tag_def_map,
        resolve_for_request,
    )

    tag_def_map = load_tag_def_map()
    base = _moments_visible_query(db, user_id)
    hard_lang = resolve_companion_list_lang(
        db, user_id, filter_lang="", accept_lang=ui_lang
    )

    def count_fn(agent_tags, agent_match, lang_eq):
        q = apply_agent_tag_filters(base, CompanionORM.tags, agent_tags, tag_def_map, agent_match)
        use_lang = lang_eq or hard_lang
        if use_lang:
            q = apply_hard_filters(q, CompanionORM, use_lang, "", "")
        return int(q.count() or 0)

    pool = resolve_for_request(
        db,
        surface="moments",
        user_id=user_id,
        ui_lang=ui_lang,
        count_fn=count_fn,
        logged_in=bool(user_id),
    )
    query = apply_waterfall_to_companion_query(base, pool, tag_def_map, CompanionORM)
    if recommend_debug is not None:
        recommend_debug.update(pool.as_debug())
        recommend_debug["_pool"] = pool
        recommend_debug["_tag_def_map"] = tag_def_map
    return query, pool


def _moments_empty_query(db):
    from sqlalchemy import false

    return _moments_visible_query(db, None).filter(false())


def _order_moments_feed_query(
    db,
    query,
    *,
    pool,
    tag_def_map: Optional[dict],
    target_lang: str,
    use_popularity: bool,
    apply_strategy_rank: bool = True,
):
    """朋友圈 feed 排序：策略优先级 →（可选）同语言优先 → 亲密度/发布时间。"""
    from services.moment_schedule import moment_effective_at
    from services.recommend import strategy_rank_order_clause

    display_at = moment_effective_at()
    order_prefix = []
    if apply_strategy_rank and pool is not None:
        rank_clause = strategy_rank_order_clause(
            CompanionORM.tags,
            pool,
            tag_def_map or {},
        )
        if rank_clause is not None:
            order_prefix.append(rank_clause.asc())

    if use_popularity:
        from core.database import UserCompanionStateORM as _UCS

        pop_subq = (
            db.query(
                _UCS.companion_id,
                func.sum(_UCS.affection).label("pop_aff"),
            )
            .group_by(_UCS.companion_id)
            .subquery()
        )
        query = query.outerjoin(
            pop_subq, pop_subq.c.companion_id == CompanionORM.id
        )
        if target_lang:
            return query.order_by(
                *order_prefix,
                case((CompanionORM.language == target_lang, 0), else_=1),
                func.coalesce(pop_subq.c.pop_aff, 0).desc(),
                desc(display_at),
            )
        return query.order_by(
            *order_prefix,
            func.coalesce(pop_subq.c.pop_aff, 0).desc(),
            desc(display_at),
        )

    if target_lang:
        return query.order_by(
            *order_prefix,
            case((CompanionORM.language == target_lang, 0), else_=1),
            desc(display_at),
        )
    return query.order_by(*order_prefix, desc(display_at))


def _build_moments_primary_and_lang_fills(
    db,
    *,
    user_id: Optional[int],
    lang: str,
    filter_lang: str,
    gender: str,
    orientation: str,
    recommend_debug=None,
    waterfall: bool = True,
):
    """主推荐池 +（未命中策略时）同语言 / 其他语言补全。

    命中 apply_to=moments 的策略后只出主池（agent_tags 条件保留），段内仍可轮转穿插。
    未命中策略：同语言 → 其他语言。显式 filter_lang 时不做其他语言段。
    """
    from services.recommend import (
        apply_hard_filters,
        resolve_companion_list_lang,
        should_fill_moments_lang_tiers,
        short_lang,
    )

    hard_lang = resolve_companion_list_lang(
        db, user_id, filter_lang=filter_lang, accept_lang=lang
    )
    explicit_lang = bool((filter_lang or "").strip())
    target_lang = short_lang(hard_lang or lang)

    pool = None
    tag_def_map = {}
    if waterfall:
        primary, pool = resolve_moments_pool(
            db, user_id, lang or "", recommend_debug=recommend_debug
        )
        if recommend_debug is not None:
            tag_def_map = recommend_debug.get("_tag_def_map") or {}
    else:
        primary = _moments_visible_query(db, user_id)

    primary = apply_hard_filters(
        primary, CompanionORM, hard_lang, gender, orientation
    )

    primary_ids = primary.order_by(None).with_entities(MomentORM.id)
    strategy_matched = bool(pool and getattr(pool, "layer", None) == "strategy")
    allow_lang_fill = should_fill_moments_lang_tiers(strategy_matched=strategy_matched)

    if allow_lang_fill:
        base_fill = _moments_visible_query(db, user_id)
        base_fill = apply_hard_filters(base_fill, CompanionORM, "", gender, orientation)
        base_fill = base_fill.filter(~MomentORM.id.in_(primary_ids))
        if target_lang:
            same_lang_q = base_fill.filter(CompanionORM.language == target_lang)
        else:
            same_lang_q = _moments_empty_query(db)
        if target_lang and not explicit_lang:
            other_lang_q = base_fill.filter(CompanionORM.language != target_lang)
        else:
            other_lang_q = _moments_empty_query(db)
    else:
        same_lang_q = _moments_empty_query(db)
        other_lang_q = _moments_empty_query(db)

    use_popularity = bool(pool.popularity_fallback) if pool else False
    return {
        "primary": primary,
        "same_lang": same_lang_q,
        "other_lang": other_lang_q,
        "pool": pool,
        "tag_def_map": tag_def_map,
        "target_lang": target_lang,
        "use_popularity": use_popularity,
    }


def _count_moments_query(query) -> int:
    return int(
        query.order_by(None).with_entities(func.count(MomentORM.id)).scalar() or 0
    )


def count_moments_feed(
    filter_lang: str = "",
    gender: str = "",
    orientation: str = "",
    user_id: Optional[int] = None,
    lang: str = "",
    recommend_debug=None,
    waterfall: bool = True,
) -> int:
    """与 get_moments_feed 相同的筛选条件下统计总数（用于分页）。"""
    from services.recommend import window_for_three_tiers

    with get_db() as db:
        built = _build_moments_primary_and_lang_fills(
            db,
            user_id=user_id,
            lang=lang or "",
            filter_lang=filter_lang or "",
            gender=gender or "",
            orientation=orientation or "",
            recommend_debug=recommend_debug,
            waterfall=waterfall,
        )
        n1 = _count_moments_query(built["primary"])
        n2 = _count_moments_query(built["same_lang"])
        n3 = _count_moments_query(built["other_lang"])
        if recommend_debug is not None:
            recommend_debug["same_lang_fill_total"] = n2
            recommend_debug["other_lang_fill_total"] = n3
            if n2 or n3:
                recommend_debug["moments_lang_fill"] = True
        *_, total = window_for_three_tiers(n1, n2, n3, 0, 1)
        return int(total)

# 首页列表
def record_moment_stats(
    moment_ids: List[int],
    *,
    kind: str,
    user_id: Optional[int] = None,
    device_id: str = "",
) -> None:
    """把本页展示或详情点击写入 moment_stat_events，并同步 moments 的次数/人数。

    次数每次请求 +1；人数仅在该用户（无登录则设备）第一次出现时 +1。
    失败只记日志，不打断列表/详情接口。
    """
    if kind not in ("view", "detail_click"):
        return
    ids: List[int] = []
    seen = set()
    for raw in moment_ids or []:
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or mid in seen:
            continue
        seen.add(mid)
        ids.append(mid)
    if not ids:
        return

    device = (device_id or "").strip()[:64]
    if device == "anonymous":
        device = ""
    uid = int(user_id) if user_id else None
    count_col = MomentORM.view_count if kind == "view" else MomentORM.detail_click_count
    users_col = MomentORM.view_users if kind == "view" else MomentORM.detail_click_users
    try:
        with get_db() as db:
            if uid:
                rows = (
                    db.query(MomentStatEventORM.moment_id)
                    .filter(
                        MomentStatEventORM.moment_id.in_(ids),
                        MomentStatEventORM.event_kind == kind,
                        MomentStatEventORM.user_id == uid,
                    )
                    .distinct()
                    .all()
                )
                already = {int(r[0]) for r in rows}
            elif device:
                rows = (
                    db.query(MomentStatEventORM.moment_id)
                    .filter(
                        MomentStatEventORM.moment_id.in_(ids),
                        MomentStatEventORM.event_kind == kind,
                        MomentStatEventORM.user_id.is_(None),
                        MomentStatEventORM.device_id == device,
                    )
                    .distinct()
                    .all()
                )
                already = {int(r[0]) for r in rows}
            else:
                already = set(ids)
            new_uv = [mid for mid in ids if mid not in already]
            db.query(MomentORM).filter(MomentORM.id.in_(ids)).update(
                {count_col: func.coalesce(count_col, 0) + 1},
                synchronize_session=False,
            )
            if new_uv:
                db.query(MomentORM).filter(MomentORM.id.in_(new_uv)).update(
                    {users_col: func.coalesce(users_col, 0) + 1},
                    synchronize_session=False,
                )
            now = datetime.now(timezone.utc)
            db.add_all(
                [
                    MomentStatEventORM(
                        moment_id=mid,
                        user_id=uid,
                        device_id=device,
                        event_kind=kind,
                        created_at=now,
                    )
                    for mid in ids
                ]
            )
    except Exception:
        logging.getLogger(__name__).exception("record_moment_stats failed kind=%s", kind)


def get_moments_feed(
    limit: int = 20,
    offset: int = 0,
    user_id: Optional[int] = None,
    lang: str = "",
    filter_lang: str = "",
    gender: str = "",
    orientation: str = "",
    recommend_debug=None,
    waterfall: bool = True,
) -> List[dict]:
    """获取朋友圈 feed，包含点赞状态。返回所有人的 companions 发布的朋友圈。

    命中推荐策略时只出主池（agent_tags 必须匹配），段内按智能体轮转穿插。
    未命中策略时：同语言补全 → 其他语言补全，段内同样穿插。
    软删：已软删智能体的动态对所有人不可见（消息列表等其它接口不受影响）。
    """
    from services.recommend import interleave_companion_rounds

    with get_db() as db:
        built = _build_moments_primary_and_lang_fills(
            db,
            user_id=user_id,
            lang=lang or "",
            filter_lang=filter_lang or "",
            gender=gender or "",
            orientation=orientation or "",
            recommend_debug=recommend_debug,
            waterfall=waterfall,
        )
        pool = built["pool"]
        tag_def_map = built["tag_def_map"]
        target_lang = built["target_lang"]
        use_popularity = built["use_popularity"]

        # 先 count（排序 outerjoin 会污染同一 query 对象）
        n1 = _count_moments_query(built["primary"])
        n2 = _count_moments_query(built["same_lang"])
        n3 = _count_moments_query(built["other_lang"])
        if recommend_debug is not None:
            recommend_debug["same_lang_fill_total"] = n2
            recommend_debug["other_lang_fill_total"] = n3
            if n2 or n3:
                recommend_debug["moments_lang_fill"] = True
            recommend_debug["moments_round_interleave"] = True

        def _id_cid_pairs(query) -> List[tuple]:
            return [
                (row[0], row[1])
                for row in query.with_entities(MomentORM.id, MomentORM.companion_id).all()
            ]

        primary_pairs: List[tuple] = []
        same_pairs: List[tuple] = []
        other_pairs: List[tuple] = []
        if n1:
            primary_q = _order_moments_feed_query(
                db,
                built["primary"],
                pool=pool,
                tag_def_map=tag_def_map,
                target_lang=target_lang,
                use_popularity=use_popularity,
                apply_strategy_rank=True,
            )
            primary_pairs = _id_cid_pairs(primary_q)
        if n2:
            same_q = _order_moments_feed_query(
                db,
                built["same_lang"],
                pool=None,
                tag_def_map=None,
                target_lang=target_lang,
                use_popularity=True,
                apply_strategy_rank=False,
            )
            same_pairs = _id_cid_pairs(same_q)
        if n3:
            other_q = _order_moments_feed_query(
                db,
                built["other_lang"],
                pool=None,
                tag_def_map=None,
                target_lang="",
                use_popularity=True,
                apply_strategy_rank=False,
            )
            other_pairs = _id_cid_pairs(other_q)

        mixed_ids = (
            interleave_companion_rounds(primary_pairs)
            + interleave_companion_rounds(same_pairs)
            + interleave_companion_rounds(other_pairs)
        )
        off = max(int(offset or 0), 0)
        lim = max(int(limit or 20), 1)
        page_ids = mixed_ids[off : off + lim]
        if not page_ids:
            return []

        rows = db.query(MomentORM).filter(MomentORM.id.in_(page_ids)).all()
        by_id = {m.id: m for m in rows}
        moments = [by_id[mid] for mid in page_ids if mid in by_id]
        if not moments:
            return []

        # 提取 moment IDs
        moment_ids = [m.id for m in moments]
        companion_ids = list({m.companion_id for m in moments})

        # ========== 第二步：批量查询 companions 和 comments 计数 ==========
        companions_map = {}
        if companion_ids:
            companions_rows = db.query(CompanionORM).filter(CompanionORM.id.in_(companion_ids)).all()
            companions_map = {c.id: c for c in companions_rows}

        comments_counts = {}
        if moment_ids:
            counts = (
                db.query(MomentCommentORM.moment_id, func.count(MomentCommentORM.id))
                .filter(MomentCommentORM.moment_id.in_(moment_ids))
                .group_by(MomentCommentORM.moment_id)
                .all()
            )
            comments_counts = {m_id: cnt for m_id, cnt in counts}

        # ========== 第三步：查询点赞状态 ==========
        liked_ids = set()
        if user_id:
            likes = (
                db.query(MomentLikeORM.moment_id)
                .filter(
                    MomentLikeORM.moment_id.in_(moment_ids),
                    MomentLikeORM.user_id == user_id,
                )
                .all()
            )
            liked_ids = {l.moment_id for l in likes}

        # ========== 组装结果 ==========
        return [
            {
                "id": m.id,
                "companion_id": m.companion_id,
                "companion_name": companions_map.get(m.companion_id, None).name if companions_map.get(m.companion_id) else _("已注销"),
                "companion_avatar": _avatar_fallback(
                    m.companion_id,
                    companions_map.get(m.companion_id, None).avatar_url if companions_map.get(m.companion_id) else "",
                ),
                "companion_gender": companions_map.get(m.companion_id, None).gender if companions_map.get(m.companion_id) else "",
                **_moment_public_image_fields(m),
                "caption": m.caption,
                "likes_count": m.likes_count,
                "comments_count": comments_counts.get(m.id, 0),
                "created_at": serialize_datetime(m.created_at),
                "liked": m.id in liked_ids,
            }
            for m in moments
        ]


def toggle_like(moment_id: int, user_id: int, tz_name: Optional[str] = None) -> dict:
    """点赞或取消点赞 - 要求必须登录"""
    if not user_id:
        return {"ok": False, "error": auth_required_message()}

    with get_db() as db:
        # 只使用 user_id 查询
        existing = (
            db.query(MomentLikeORM)
            .filter_by(moment_id=moment_id, user_id=user_id)
            .first()
        )

        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return {"ok": False, "error": "Moment not found"}

        newly_liked = False
        companion_id = moment.companion_id
        caption = moment.caption or ""
        if existing:
            db.delete(existing)
            new_count = max((moment.likes_count or 0) - 1, 0)
            moment.likes_count = new_count
            db.flush()
            liked = False
            likes_count = new_count
        else:
            like_device = f"u:{int(user_id)}"
            try:
                db.add(MomentLikeORM(
                    moment_id=moment_id,
                    user_id=user_id,
                    device_id=like_device,
                ))
                moment.likes_count = (moment.likes_count or 0) + 1
                db.flush()
                liked = True
                newly_liked = True
            except IntegrityError:
                # 撞唯一键时不能假成功：回查自己有没有赞，并重新读计数
                db.rollback()
                own = (
                    db.query(MomentLikeORM)
                    .filter_by(moment_id=moment_id, user_id=user_id)
                    .first()
                )
                moment = db.query(MomentORM).filter_by(id=moment_id).first()
                if own:
                    liked = True
                    newly_liked = False
                elif moment:
                    try:
                        db.add(MomentLikeORM(
                            moment_id=moment_id,
                            user_id=user_id,
                            device_id=like_device,
                        ))
                        moment.likes_count = (moment.likes_count or 0) + 1
                        db.flush()
                        liked = True
                        newly_liked = True
                    except IntegrityError:
                        db.rollback()
                        liked = (
                            db.query(MomentLikeORM)
                            .filter_by(moment_id=moment_id, user_id=user_id)
                            .first()
                        ) is not None
                        newly_liked = False
                        moment = db.query(MomentORM).filter_by(id=moment_id).first()
                else:
                    liked = False
            likes_count = int((moment.likes_count if moment else 0) or 0)

        result = {"ok": True, "liked": liked, "likes_count": likes_count, "_newly_liked": newly_liked}

    # 会话外调度/取消触达，避免拖长事务
    try:
        from services.outreach import cancel_like_outreach, schedule_like_outreach

        newly = bool(result.pop("_newly_liked", liked))
        if not liked:
            cancel_like_outreach(user_id=user_id, moment_id=moment_id)
        elif newly:
            companion_name = ""
            try:
                from core.state import get_companion_manager

                cm = get_companion_manager()
                c = cm.get(companion_id) if cm and companion_id else None
                if c:
                    companion_name = getattr(c.profile, "name", "") or ""
            except Exception:
                pass
            schedule_like_outreach(
                user_id=user_id,
                moment_id=moment_id,
                companion_id=companion_id or "",
                caption=caption,
                companion_name=companion_name,
                tz_name=tz_name,
            )
    except Exception as e:
        logger.warning("[Moments] like outreach hook failed: %s", e)

    return result


def get_companion_moments(
    companion_id: str,
    limit: int = 20,
    user_id: Optional[int] = None,
) -> List[dict]:
    """获取某个伴侣的所有朋友圈。创建者软删后自己侧空列表；其他人仍可读。"""
    from services.companion_report import assert_companion_visible_to_user

    if user_id and assert_companion_visible_to_user(int(user_id), companion_id):
        return []
    with get_db() as db:
        companion = db.query(CompanionORM).filter_by(id=companion_id).first()
        if not companion:
            return []
        owner_keys = _owner_keys_for_user(user_id, db=db)
        if companion.is_deleted and (
            owner_keys is None or _viewer_owns_companion_row(companion, owner_keys)
        ):
            return []

        from services.moment_schedule import apply_published_moment_filter, moment_effective_at

        moments = (
            apply_published_moment_filter(
                db.query(MomentORM).filter_by(companion_id=companion_id)
            )
            .order_by(desc(moment_effective_at()))
            .limit(limit)
            .all()
        )
        liked_ids = set()
        if user_id and moments:
            moment_ids = [m.id for m in moments]
            likes = (
                db.query(MomentLikeORM.moment_id)
                .filter(
                    MomentLikeORM.moment_id.in_(moment_ids),
                    MomentLikeORM.user_id == user_id,
                )
                .all()
            )
            liked_ids = {row[0] for row in likes}
        return [
            {
                "id": m.id,
                **_moment_public_image_fields(m),
                "caption": m.caption,
                "caption_lang": getattr(m, "caption_lang", None),
                "likes_count": m.likes_count,
                "comments_count": m.comments_count,
                "created_at": serialize_datetime(m.created_at),
                "liked": m.id in liked_ids,
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
    """为所有伴侣自动随机生成朋友圈，返回生成的数量。
    开关：core.set.AUTO_MOMENTS_OFF（1=关，0=开）。
    """
    # 硬关闭自动发圈（后台调度 / warmup 最终都进这里）
    logger.info("[Moments] 自动发朋友圈已硬关闭，跳过 auto_generate_moments_for_all")
    return 0

    from core.set import auto_moments_enabled

    if not auto_moments_enabled():
        return 0
    created = 0
    companions = companion_manager.list_all()
    for c in companions:
        # 跳过已删除/已注销的智能体
        if c.get("is_deleted") or c.get("profile", {}).get("is_deleted"):
            continue
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
- 1-2句话，可以吐槽或追问细节，不要模板感叹句
- emoji 可不加；要加最多1个
- 语气像真朋友，避免「哈哈好可爱」式万能回复
- 只输出评论内容"""


def _clip_moment_comment(text: str, max_len: int = 280) -> str:
    """朋友圈评论/回复截断：优先在句末或空格处切开，避免 100 字硬切导致半句话。"""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in ("。", "！", "？", "…", ".", "!", "?", "\n", " "):
        idx = cut.rfind(sep)
        if idx >= max_len // 2:
            end = idx + (0 if sep == " " else 1)
            return cut[:end].strip()
    return cut.rstrip()


def generate_ai_comment(commenter: Companion, poster: Companion, caption: str, lang: str = "zh") -> str:
    """调用 LLM 为 AI 伴侣生成一条朋友圈评论"""
    try:
        llm = get_llm(temperature=0.9)
        prompt = _build_comment_prompt(commenter, poster, caption, lang)
        resp = llm.invoke([("human", prompt)])
        text = llm_content_to_str(getattr(resp, "content", "")).strip()
        text = text.strip('"').strip("'").strip()
        return _clip_moment_comment(text)
    except Exception as e:
        logger.warning("[Moments] AI 评论生成失败: %s", e)
        fallbacks = {
            "zh": ["这光也太骗人了吧", "下一张呢", "认真的吗，还挺像那么回事", "细节说说", "你这属实会过日子"],
            "en": ["So cute!", "Wish I was there!", "Looks amazing", "Love this!", "Miss you!"],
            "ja": ["かわいい！", "私も行きたい！", "羨ましい～", "美味しそう", "元気そうでよかった"],
            "ko": ["진짜 귀엽다!", "나도 가고 싶어!", "부러워~", "맛있어 보여", "기분 좋아 보이네"],
            "pt": ["Que fofo!", "Também quero ir!", "Que inveja boa~", "Parece delicioso", "Parece que tá feliz"],
            "es": ["¡Qué lindo!", "¡Yo también quiero ir!", "Qué envidia~", "Se ve delicioso", "Parece que estás feliz"],
            "id": ["Lucu banget!", "Aku juga mau ke sana!", "Iri~", "Kelihatan enak", "Kelihatan bahagia"],
            "ar": ["حلو!", "يا ليتني هناك", "شكله رائع", "حبيت", "اشتقت"],
            "fa": ["چه ناز!", "کاش منم بودم", "فوق‌العاده است", "دوست داشتم", "دلم تنگ شد"],
            "ru": ["Мило!", "Жаль, что меня нет", "Супер", "Нравится", "Скучаю"],
            "bg": ["Мило!", "И аз искам там", "Страхотно", "Харесвам", "Липсваш ми"],
            "tr": ["Çok tatlı!", "Keşke orada olsam", "Harika duruyor", "Bayıldım", "Özledim"],
        }
        return random.choice(fallbacks.get(lang, fallbacks["en"]))


# 朋友圈评论回复：输出语种跟用户评论，不跟人设/App 界面语言
# 成文强制句来自 get_pack(lang)["reply_lang_turn"]


def _build_reply_prompt(poster: Companion, caption: str, user_comment: str, lang: str = "zh") -> str:
    """构建让发帖 AI 回复用户评论的 prompt。

    lang 跟被回复那条评论的语言；壳与 reply_lang_turn 同语。
    """
    p_name = poster.profile.name
    p_personality = poster.profile.personality

    if lang == "en":
        prompt = f"""You are {p_name}, a real person with personality: {p_personality}.

You posted on social media: "{caption}"

A user just commented on your post: "{user_comment}"

Task: Write a short, natural reply as {p_name}. It should feel like a real person replying to a friend's comment — warm, casual, and genuine.

Requirements:
- 1-2 sentences; ask a detail or tease lightly; avoid canned praise
- Emoji optional (max 1)
- Casual and specific to the post
- Output ONLY the reply text, nothing else"""

    elif lang == "ja":
        prompt = f"""あなたは{p_name}という実在の人物です。性格：{p_personality}。

あなたはSNSに「{caption}」と投稿しました。

ユーザーがあなたの投稿に「{user_comment}」とコメントしました。

タスク：{p_name}として自然な短い返信を書いてください。本当の人が友達のコメントに返信するような、温かくてカジュアルな感じにしてください。

要件：
- 1文以内（非常に短く）
- 自然に絵文字を0〜1個入れる
- カジュアルで人間味のある感じ
- 返信文のみ出力し、それ以外は何も書かない"""

    elif lang == "ko":
        prompt = f"""당신은 {p_name}라는 실존 인물입니다. 성격: {p_personality}.

당신은 SNS에 "{caption}"라고 올렸습니다.

사용자가 당신의 포스트에 "{user_comment}"라고 댓글을 달았습니다.

과제: {p_name}로서 자연스러운 짧은 답글을 써주세요. 진짜 사람이 친구 댓글에 답하는 듯한, 따뜻하고 캐주얼한 느낌으로.

요구사항:
- 1문장 이내 (매우 짧게)
- 자연스럽게 이모지 0~1개 포함
- 캐주얼하고 인간적인 느낌
- 답글 본문만 출력하고, 그 외에는 아무것도 쓰지 마세요"""

    elif lang == "pt":
        prompt = f"""Você é {p_name}, uma pessoa real com personalidade: {p_personality}.

Você postou nas redes sociais: "{caption}"

Um usuário acabou de comentar na sua postagem: "{user_comment}"

Tarefa: Escreva uma resposta curta e natural como {p_name}. Deve parecer uma pessoa de verdade respondendo o comentário de um amigo — calorosa, casual e genuína.

Requisitos:
- Máximo 1 frase (muito curto)
- Inclua 0-1 emoji naturalmente
- Casual e humano
- Saída APENAS o texto da resposta, nada mais"""

    elif lang == "es":
        prompt = f"""Eres {p_name}, una persona real con personalidad: {p_personality}.

Publicaste en redes sociales: "{caption}"

Un usuario acaba de comentar en tu publicación: "{user_comment}"

Tarea: Escribe una respuesta corta y natural como {p_name}. Debe sentirse como una persona real respondiendo el comentario de un amigo — cálida, casual y genuina.

Requisitos:
- Máximo 1 oración (muy corto)
- Incluye 0-1 emoji de forma natural
- Casual y humano
- Salida SOLO el texto de la respuesta, nada más"""

    elif lang == "id":
        prompt = f"""Kamu adalah {p_name}, manusia nyata dengan kepribadian: {p_personality}.

Kamu memposting di media sosial: "{caption}"

Seorang pengguna baru saja berkomentar di postinganmu: "{user_comment}"

Tugas: Tulis balasan pendek dan natural sebagai {p_name}. Harus terasa seperti manusia nyata yang membalas komentar teman — hangat, santai, dan tulus.

Persyaratan:
- Maksimal 1 kalimat (sangat pendek)
- Sertakan 0-1 emoji secara natural
- Santai dan terasa manusiawi
- Keluarkan HANYA teks balasan, tidak ada yang lain"""

    elif lang == "zh":
        prompt = f"""你是{p_name}，一个真实的人，性格：{p_personality}。

你发了一条朋友圈："{caption}"

有用户在你的朋友圈下评论："{user_comment}"

任务：以{p_name}的身份写一条简短自然的回复。要像真朋友回复评论一样——温暖、随意、真诚。

要求：
- 最多1句话（非常短）
- 自然地带上0-1个emoji
- 语气要像真人回复
- 只输出回复内容，不要任何其他内容"""

    elif lang == "ar":
        prompt = f"""أنت {p_name}، شخص حقيقي بشخصية: {p_personality}.

نشرت على وسائل التواصل: "{caption}"

علّق مستخدم على منشورك: "{user_comment}"

المهمة: اكتب رداً قصيراً وطبيعياً بصفتك {p_name}. دافئ وعفوي وصادق.

المتطلبات:
- جملة واحدة قصيرة
- إيموجي اختياري (0–1)
- إخراج نص الرد فقط"""

    elif lang == "fa":
        prompt = f"""تو {p_name} هستی، آدم واقعی با شخصیت: {p_personality}.

پست کردی: "{caption}"

کاربری کامنت گذاشت: "{user_comment}"

وظیفه: پاسخ کوتاه و طبیعی به‌عنوان {p_name}. گرم، خودمانی، صادق.

الزامات:
- حداکثر یک جمله
- ایموجی اختیاری (۰–۱)
- فقط متن پاسخ"""

    elif lang == "ru":
        prompt = f"""Ты {p_name}, реальный человек с характером: {p_personality}.

Ты опубликовал(а): "{caption}"

Пользователь прокомментировал: "{user_comment}"

Задача: короткий естественный ответ от лица {p_name}. Тепло, по-человечески.

Требования:
- максимум 1 предложение
- эмодзи опционально (0–1)
- только текст ответа"""

    elif lang == "bg":
        prompt = f"""Ти си {p_name}, реален човек с характер: {p_personality}.

Публикува: "{caption}"

Потребител коментира: "{user_comment}"

Задача: кратък естествен отговор като {p_name}. Топло и човешко.

Изисквания:
- максимум 1 изречение
- емоджи по желание (0–1)
- само текстът на отговора"""

    elif lang == "tr":
        prompt = f"""Sen {p_name}’sin, kişilik: {p_personality}.

Şunu paylaştın: "{caption}"

Bir kullanıcı yorumladı: "{user_comment}"

Görev: {p_name} olarak kısa, doğal bir yanıt yaz. Sıcak, samimi.

Kurallar:
- en fazla 1 cümle
- isteğe bağlı 0–1 emoji
- yalnızca yanıt metni"""

    else:
        # 未知 pack：英文结构壳 + reply_lang_turn（勿默认中文壳）
        prompt = f"""You are {p_name}, a real person with personality: {p_personality}.

You posted on social media: "{caption}"

A user just commented on your post: "{user_comment}"

Task: Write a short, natural reply as {p_name}. Warm, casual, genuine.

Requirements:
- 1-2 sentences max
- Emoji optional (max 1)
- Output ONLY the reply text"""

    return prompt + get_pack(lang)["reply_lang_turn"]


def generate_ai_reply_to_user(poster: Companion, caption: str, user_comment: str, lang: str = "zh") -> str:
    """调用 LLM 让发帖 AI 回复用户评论"""
    try:
        llm = get_llm(temperature=0.9)
        prompt = _build_reply_prompt(poster, caption, user_comment, lang)
        resp = llm.invoke([("human", prompt)])
        text = llm_content_to_str(getattr(resp, "content", "")).strip()
        text = text.strip('"').strip("'").strip()
        return _clip_moment_comment(text)
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
            "ar": ["هههه شكراً", "مسكتني~", "المرة الجاية سوا", "تعليقك فرّحني", "حب"],
            "fa": ["هاها مرسی", "گیرم انداختی~", "دفعه بعد با هم", "کامنتت حال داد", "دوستت دارم"],
            "ru": ["Хаха спасибо!", "Поймал~", "В следующий раз вместе", "Коммент поднял настроение", "Люблю"],
            "bg": ["Хаха благодаря!", "Хвана ме~", "Следващия път заедно", "Коментарът ме зарадва", "Обичам те"],
            "tr": ["Haha teşekkürler!", "Yakaladın~", "Bir dahaki sefere beraber", "Yorumun günümü kurtardı", "Seviliyosun"],
        }
        return random.choice(fallbacks.get(lang, fallbacks["en"]))


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
- 1-2 sentences; ask a detail or tease lightly; avoid canned praise
- Emoji optional (max 1)
- Casual and specific to the post
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
        llm = get_llm(temperature=0.9)
        prompt = _build_reply_to_ai_prompt(poster, caption, ai_comment, commenter_name, lang)
        resp = llm.invoke([("human", prompt)])
        text = llm_content_to_str(getattr(resp, "content", "")).strip()
        text = text.strip('"').strip("'").strip()
        return _clip_moment_comment(text)
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
            "ar": ["هههه أنت بعد!", "مسكتني~", "المرة الجاية سوا", "تعرفني زين", "حب"],
            "fa": ["هاها تو هم!", "گیرم انداختی~", "دفعه بعد با هم", "خوب می‌شناسیم", "دوستت دارم"],
            "ru": ["Хаха ты тоже!", "Поймал~", "В следующий раз вместе", "Ты меня знаешь", "Люблю"],
            "bg": ["Хаха и ти!", "Хвана ме~", "Следващия път заедно", "Познаваш ме", "Обичам те"],
            "tr": ["Haha sen de!", "Yakaladın~", "Bir dahaki sefere beraber", "Beni iyi biliyosun", "Seviliyosun"],
        }
        return random.choice(fallbacks.get(lang, fallbacks["en"]))


def auto_generate_ai_comments(companion_manager) -> int:
    """为最新的朋友圈自动生成 AI 之间的互动评论（发帖者为非中文时上限与回评率更高）。
    与自动发圈共用 core.set.AUTO_MOMENTS_OFF。
    """
    # 硬关闭：与自动发圈一并停掉 AI 互评
    logger.info("[Moments] 自动发朋友圈已硬关闭，跳过 auto_generate_ai_comments")
    return 0

    from core.set import auto_moments_enabled

    if not auto_moments_enabled():
        return 0
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
                "caption_lang": getattr(m, "caption_lang", None) or "",
            }
            for m in recent_moments
        ]

    for moment in moments_data:
        poster_id = moment["companion_id"]
        poster = companion_manager.get(poster_id)
        if not poster:
            continue
        # 跳过已删除/已注销智能体的朋友圈
        if getattr(poster.profile, "is_deleted", False):
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

            # 有正文跟帖子语言；无内容则评论者人设语言
            lang = _ai_comment_lang(
                moment.get("caption"),
                commenter,
                caption_lang=moment.get("caption_lang"),
                poster=poster,
            )

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
                    reply_lang = _resolve_moment_interact_lang(comment_text, lang)

                    reply_text = generate_ai_reply_to_ai(
                        poster, moment["caption"], comment_text, commenter.profile.name, reply_lang
                    )

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


def _auto_review_comment(content: str) -> dict:
    """
    自动审核评论内容（关键词 + 规则启发式）。
    返回 {"approved": bool, "reason": str, "toast": str}
    """
    text = (content or "").strip().lower()
    raw = (content or "").strip()

    # 违禁关键词列表（可根据业务扩展）
    _BANNED_KEYWORDS = [
        # 广告引流
        "加微信", "加v", "加vx", "加微", "v信", "vx:", "微信:",
        "扫码", "免费领", "点击链接", "http://", "https://", "t.me/",
        "代充", "代付", "刷单", "兼职赚钱", "日赚", "稳赚",
        # 色情/暴力
        "裸聊", "约炮", "色情", "赌博", "博彩", "赌场",
        # 英文
        "buy now", "click here", "free money", "nude", "porn", "gamble",
        "whatsapp", "telegram.me",
    ]

    for kw in _BANNED_KEYWORDS:
        if kw in text:
            return {
                "approved": False,
                "reason": "含违规内容",
                "toast": _("评论包含违规内容，请修改后重试"),
            }

    # 去掉空白和非文字后为空：纯符号、纯表情等视为无效评论（不是“空评论”）
    cleaned = re.sub(r"[\s\W]", "", text)
    if len(raw) > 0 and len(cleaned) == 0:
        return {
            "approved": False,
            "reason": "无意义内容",
            "toast": _("评论内容无效，请修改后重试"),
        }

    # 过长重复字符 / 疑似刷屏
    if len(raw) >= 8 and len(set(raw.replace(" ", ""))) <= 2:
        return {
            "approved": False,
            "reason": "疑似刷屏",
            "toast": _("评论内容无效，请修改后重试"),
        }
    if re.search(r"(.)\1{7,}", raw):
        return {
            "approved": False,
            "reason": "疑似刷屏",
            "toast": _("评论内容无效，请修改后重试"),
        }

    # 联系方式启发式（手机号 / 邮箱）
    if re.search(r"(?<!\d)(?:1[3-9]\d{9})(?!\d)", raw) or re.search(
        r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text
    ):
        return {
            "approved": False,
            "reason": "含联系方式",
            "toast": _("评论不可包含联系方式"),
        }

    return {"approved": True}


_COMMENT_REPLY_NOTIF_TITLES = {
    "zh": "{name}回复了你的评论",
    "en": "{name} replied to your comment",
    "ja": "{name}があなたのコメントに返信しました",
    "ko": "{name}님이 댓글에 답했어요",
    "pt": "{name} respondeu ao seu comentário",
    "es": "{name} respondió a tu comentario",
    "id": "{name} membalas komentarmu",
}


def build_comment_reply_notification(
    *,
    companion_name: str,
    reply_text: str,
    ui_lang: Optional[str] = None,
) -> dict:
    """系统通知：标题说明「谁回了评论」，正文就是朋友圈下的回复原文。"""
    lang = normalize_ui_language(ui_lang)
    name = (companion_name or "").strip() or ("TA" if lang == "zh" else "Someone")
    tmpl = _COMMENT_REPLY_NOTIF_TITLES.get(lang) or _COMMENT_REPLY_NOTIF_TITLES["en"]
    return {
        "title": tmpl.format(name=name)[:200],
        "content": (reply_text or "").strip()[:500],
        "language": lang,
    }


def _notify_user_of_comment_reply(
    db,
    *,
    user_id: Optional[int],
    companion_id: str,
    companion_name: str,
    reply_text: str,
    moment_id: Optional[int] = None,
    ui_lang: Optional[str] = None,
) -> None:
    if not user_id or not (reply_text or "").strip():
        return
    payload = build_comment_reply_notification(
        companion_name=companion_name,
        reply_text=reply_text,
        ui_lang=ui_lang,
    )
    db.add(
        SystemNotificationORM(
            title=payload["title"],
            content=payload["content"],
            language=payload["language"],
            user_id=int(user_id),
            companion_id=companion_id or None,
            moment_id=int(moment_id) if moment_id else None,
        )
    )
    try:
        from services.push_sender import send_push_to_user

        send_push_to_user(
            int(user_id),
            title=payload["title"][:200],
            body=payload["content"][:200],
            data={
                "type": "moment_comment_reply",
                "companion_id": companion_id or "",
                "moment_id": str(moment_id or ""),
            },
        )
    except Exception:
        pass


def add_user_comment(
    moment_id: int,
    device_id: str,
    content: str,
    parent_id: int = None,
    user_id: Optional[int] = None,
    tz_name: Optional[str] = None,
    ui_lang: Optional[str] = None,
) -> dict:
    """用户发表评论（或回复评论），并触发发帖 AI 的回复"""
    content = content.strip()
    if not content:
        return {"ok": False, "error": _("评论内容不能为空", lang=ui_lang), "error_code": "empty"}
    if len(content) > 200:
        content = content[:200]

    # 在 session 内提取所有需要的数据，避免 detached 对象问题
    moment_companion_id = None
    moment_caption = None
    moment_caption_lang = ""
    parent_content = ""

    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            return {"ok": False, "error": _("朋友圈不存在", lang=ui_lang), "error_code": "not_found"}

        # 提取 moment 数据到普通变量
        moment_companion_id = moment.companion_id
        moment_caption = moment.caption
        moment_caption_lang = getattr(moment, "caption_lang", None) or ""

        # 同一用户 + 同一动态评论上限（智能体回复不计入）
        if user_id:
            from services.moment_comment_limit import (
                COMMENT_LIMIT_CODE,
                COMMENT_LIMIT_ERROR,
                check_user_can_comment,
            )

            gate = check_user_can_comment(db, moment_id=moment_id, user_id=int(user_id))
            if not gate.get("ok"):
                used = int(gate.get("used") or 0)
                return {
                    "ok": False,
                    "error": _(
                        "本条动态评论次数已达上限，去私聊继续聊吧",
                        lang=ui_lang,
                    ),
                    "error_code": COMMENT_LIMIT_ERROR,
                    "biz_code": COMMENT_LIMIT_CODE,
                    "moment_id": moment_id,
                    "companion_id": moment_companion_id or "",
                    "limit": gate.get("limit"),
                    "used": used,
                    "commented_count": used,
                    "action": "go_im",
                }

        # 如果指定了 parent_id，验证该评论是否存在
        parent_comment = None
        if parent_id:
            parent_comment = db.query(MomentCommentORM).filter_by(id=parent_id, moment_id=moment_id).first()
            if not parent_comment:
                return {
                    "ok": False,
                    "error": _("要回复的评论不存在", lang=ui_lang),
                    "error_code": "not_found",
                }
            parent_content = parent_comment.content or ""

        comment = MomentCommentORM(
            moment_id=moment_id,
            companion_id="",
            user_id=user_id,  # 保存 user_id
            user_device_id=(device_id or "")[:64] or None,  # VARCHAR(64)；防重复 header 超长
            parent_id=parent_id,
            content=content,
        )
        db.add(comment)
        moment.comments_count = (moment.comments_count or 0) + 1
        db.flush()
        comment_id = comment.id
        comment_created_at = serialize_datetime(comment.created_at)

        # ===== 评论审核 =====
        review_result = _auto_review_comment(content)
        review_status = "approved" if review_result["approved"] else "rejected"
        review = MomentCommentReviewORM(
            comment_id=comment_id,
            user_id=user_id or 0,
            status=review_status,
            reject_reason=review_result.get("reason", ""),
            reviewed_by="auto",
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add(review)

        if not review_result["approved"]:
            # 未通过审核：从 session 移除未持久化的 review，删除已 flush 的 comment
            db.expunge(review)
            db.delete(comment)
            moment.comments_count = max(0, (moment.comments_count or 0) - 1)
            db.commit()
            return {
                "ok": False,
                "error": review_result.get("toast", _("评论未通过审核")),
                "error_code": "invalid",
                "review_status": "rejected",
            }

        db.commit()

    # 尝试让发帖 AI 回复用户评论
    ai_reply = None
    try:
        companion_manager = get_companion_manager()
        if companion_manager:
            poster = companion_manager.get(moment_companion_id)
            if poster:
                fallback = _profile_lang(poster)
                if moment_caption_lang:
                    fallback = normalize_ui_language(
                        (moment_caption_lang or "").split("-")[0].strip() or fallback
                    )
                fallback = _resolve_moment_interact_lang(moment_caption, fallback)
                if parent_content:
                    fallback = _resolve_moment_interact_lang(parent_content, fallback)
                reply_lang = _resolve_moment_interact_lang(content, fallback)
                reply_text = generate_ai_reply_to_user(
                    poster, moment_caption, content, reply_lang
                )

                with get_db() as db:
                    reply = MomentCommentORM(
                        moment_id=moment_id,
                        companion_id=moment_companion_id,
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
                        "is_me": False,  # AI 发的，不是当前用户
                        "is_reply_me": True,  # AI 回复的是用户的评论
                        "companion_id": moment_companion_id,
                        "companion_name": poster.profile.name,
                        "content": reply_text,
                        "created_at": serialize_datetime(reply.created_at),
                        "parent_id": comment_id,
                    }
                    try:
                        _notify_user_of_comment_reply(
                            db,
                            user_id=user_id,
                            companion_id=moment_companion_id,
                            companion_name=poster.profile.name,
                            reply_text=reply_text,
                            moment_id=moment_id,
                            ui_lang=ui_lang,
                        )
                    except Exception as ne:
                        logger.warning("[Moments] comment reply notification failed: %s", ne)
                    logger.info("[Moments AI] %s 回复了用户评论: %s", poster.profile.name, reply_text)

    except Exception as e:
        logger.warning("[Moments] AI 回复用户评论失败: %s", e)

    # Phase1：评论触达私聊（有无公开回复都调度；文案侧会避开雷同）
    if user_id and moment_companion_id:
        try:
            from services.outreach import schedule_comment_outreach

            public_reply = (ai_reply or {}).get("content") or ""
            companion_name = (ai_reply or {}).get("companion_name") or ""
            if not companion_name:
                try:
                    cm = get_companion_manager()
                    c = cm.get(moment_companion_id) if cm else None
                    if c:
                        companion_name = getattr(c.profile, "name", "") or ""
                except Exception:
                    pass
            schedule_comment_outreach(
                user_id=user_id,
                moment_id=moment_id,
                companion_id=moment_companion_id,
                comment_id=comment_id,
                caption=moment_caption or "",
                user_comment=content,
                public_reply=public_reply,
                companion_name=companion_name,
                tz_name=tz_name,
                ui_lang=ui_lang,
            )
        except Exception as oe:
            logger.warning("[Moments] comment outreach schedule failed: %s", oe)

    # 检查是否回复的是自己的评论
    is_reply_me = False
    # if parent_id:
    #     with get_db() as db:
    #         parent = db.query(MomentCommentORM).filter_by(id=parent_id).first()
    #         if parent and parent.user_device_id == device_id:
    #             is_reply_me = True

    result = {
        "ok": True,
        "id": comment_id,
        "content": content,
        "created_at": comment_created_at,
        "parent_id": parent_id,
        "is_me": True,  # 当前用户发的评论
        "is_reply_me": True,  # 回复的是自己的评论
    }
    if ai_reply:
        result["ai_reply"] = ai_reply
    return result


def get_moment_comments(moment_id: int, limit: int = 50, current_user_id: Optional[int] = None) -> List[dict]:
    """获取某条朋友圈的所有评论（包含 AI 评论和用户评论），支持回复关系。
    审核中的评论仅对评论者本人可见。"""
    with get_db() as db:
        comments = (
            db.query(MomentCommentORM)
            .filter_by(moment_id=moment_id)
            .order_by(MomentCommentORM.created_at)
            .limit(limit)
            .all()
        )

        # 查询评论审核状态
        comment_ids = [c.id for c in comments if c.user_id or c.user_device_id]
        review_status_map = {}  # {comment_id: status}
        if comment_ids:
            reviews = db.query(MomentCommentReviewORM).filter(
                MomentCommentReviewORM.comment_id.in_(comment_ids)
            ).all()
            for rv in reviews:
                review_status_map[rv.comment_id] = rv.status

        # 过滤：审核中的用户评论仅对评论者本人可见
        filtered_comments = []
        for c in comments:
            # AI 评论直接通过
            if not c.user_id and not c.user_device_id:
                filtered_comments.append(c)
                continue
            # 用户评论：检查审核状态
            review_status = review_status_map.get(c.id)
            if review_status == "approved":
                filtered_comments.append(c)
            elif review_status == "rejected":
                # 已拒绝：仅评论者可见（显示已被删除的提示）
                if current_user_id and c.user_id == current_user_id:
                    filtered_comments.append(c)
                # 其他人看不到
            elif review_status == "pending":
                # 审核中：仅评论者本人可见
                if current_user_id and c.user_id == current_user_id:
                    filtered_comments.append(c)
                # 其他人看不到
            else:
                # 无审核记录（旧数据/历史评论）：全部可见
                filtered_comments.append(c)

        comments = filtered_comments
        # 批量获取 AI 评论的 companion 信息
        ai_companion_ids = list({c.companion_id for c in comments if not c.user_device_id and c.companion_id})
        companions_map = {}
        if ai_companion_ids:
            companions_rows = db.query(CompanionORM).filter(CompanionORM.id.in_(ai_companion_ids)).all()
            companions_map = {c.id: c for c in companions_rows}

        # 批量获取用户评论的用户信息
        user_ids = list({c.user_id for c in comments if c.user_id})
        users_map = {}
        if user_ids:
            users_rows = db.query(UserORM).filter(UserORM.id.in_(user_ids)).all()
            users_map = {u.id: u for u in users_rows}

        # 构建评论ID到评论者信息的映射，用于回复关系和判断 is_reply_me
        comment_author_map = {}  # {comment_id: display_name}
        comment_user_id_map = {}  # {comment_id: user_id} 用于判断是否回复自己

        for c in comments:
            comment_user_id_map[c.id] = c.user_id
            if c.user_id:
                user = users_map.get(c.user_id)
                # 当前用户的评论显示"我"，其他用户的评论显示昵称
                if current_user_id and c.user_id == current_user_id:
                    comment_author_map[c.id] = _("我")
                else:
                    comment_author_map[c.id] = user.nickname if user and user.nickname else (user.username if user else _("匿名用户"))
            elif c.user_device_id:
                # 兼容旧数据：没有 user_id 但有 user_device_id 的评论
                comment_author_map[c.id] = _("匿名用户")
            else:
                companion = companions_map.get(c.companion_id)
                comment_author_map[c.id] = companion.name if companion else _("已注销")

        result = []
        for c in comments:
            reply_to_name = None
            is_reply_me = False
            if c.parent_id:
                reply_to_name = comment_author_map.get(c.parent_id)
                # 判断是否回复的是当前用户的评论
                parent_user_id = comment_user_id_map.get(c.parent_id)
                is_reply_me = current_user_id is not None and parent_user_id == current_user_id

            if c.user_id or c.user_device_id:
                # 用户评论
                # 确定显示名称：当前用户显示"我"，其他用户显示昵称
                is_me = current_user_id is not None and c.user_id == current_user_id
                if is_me:
                    display_name = _("我")
                elif c.user_id:
                    user = users_map.get(c.user_id)
                    display_name = user.nickname if user and user.nickname else (user.username if user else _("匿名用户"))
                else:
                    display_name = _("匿名用户")

                user = users_map.get(c.user_id) if c.user_id else None
                result.append({
                    "id": c.id,
                    "is_user": True,
                    "is_me": is_me,
                    "is_reply_me": is_reply_me,
                    "user_id": c.user_id,
                    "companion_id": None,
                    "companion_name": display_name,
                    "avatar_url": user.avatar_url if user else "",
                    "content": c.content,
                    "created_at": serialize_datetime(c.created_at),
                    "parent_id": c.parent_id,
                    "reply_to_name": reply_to_name,
                    "review_status": review_status_map.get(c.id, "approved"),
                })
            else:
                # AI 评论
                companion = companions_map.get(c.companion_id)
                companion_name = companion.name if companion else _("已注销")
                result.append({
                    "id": c.id,
                    "is_user": False,
                    "is_me": False,
                    "is_reply_me": is_reply_me,
                    "companion_id": c.companion_id,
                    "companion_name": companion_name,
                    "avatar_url": companion.avatar_url if companion else "",
                    "content": c.content,
                    "created_at": serialize_datetime(c.created_at),
                    "parent_id": c.parent_id,
                    "reply_to_name": reply_to_name,
                })
        return result
