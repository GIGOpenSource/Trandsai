import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from langchain_core.messages import HumanMessage, SystemMessage

from core.auth import verify_token as verify_user_token
from core.response import ok, fail, paginated
from core.database import UserCompanionStateORM, UserORM, get_db
from core.rest_async import run_rest
from core.state import get_companion_manager
from services.agent_utils import llm_content_to_str
from services.agent import get_llm
from services.llm.client import llm_invoke
from services.culture_data import (
    build_random_profile,
    get_cultural_context_for_city,
    default_cultural_values,
    get_persona_generate_output_instruction,
)
from services.persona_axes import (
    axes_prompt_block,
    localize_axes_for_display,
    resolve_axes_from_payload,
)
from services.region_catalog import find_region_for_city
from services.recommend_tags import canonical_orientation, display_sexual_orientation
from services.prompts.resolve import resolve_persona_generate_lang
from core.i18n import (
    auth_required_message,
    companion_not_found_message,
    companion_deleted_message,
    credits_insufficient_message,
    agent_limit_message,
    vip_required_message,
    normalize_ui_language,
    http_ui_lang,
    validation_message,
    _,
)
from core.gender import display_gender, stored_gender
from services.async_tasks import start_avatar_generation
from api.companion_access import (
    require_login_user,
    _assert_companion_readable,
    _assert_companion_owner,
    _assert_companion_user_access,
)
from api.companion_ws import _strip_json_think_blocks, _update_moment_messages
import asyncio
from pydantic import BaseModel, Field
from services.im_translate import baidu_to_lang_from_accept, translate_one
router = APIRouter(tags=["伴侣"])
logger = logging.getLogger(__name__)


_PERSONA_GENERATE_PROMPT = """你是一个专业的人物设定作家。请根据以下基础信息，生成一个完整、立体、真实的虚拟伙伴/伴侣人设。

要求：
0. **输出语言（强制，违反则整份作废）**：
{output_instruction}
1. 生成的内容必须和已知的基础信息（姓名、年龄、性别、城市、性格、MBTI）高度一致；城市决定生活环境与文化圈，禁止写成与该城市主流文化不符的另一国家/语言人生
2. 内容要口语化、有画面感、真实可信，不要模板化；禁止复制「童年→求学→某年搬到某城→如今…」的空洞脚手架长段
3. **性格标签必须被消化进全文**：speech_style / love_view / fears / hobbies / life_story 都要能看出给定性格（{personality}）的具体行为与语气；禁止无视种子写成「通用温柔体贴伴侣」
4. **性别气质禁止默认少女腔**：角色可为任意性别气质（沉稳/硬核/寡言/社牛/毒舌/事业心等均可）。若性别为男或性格偏硬朗，禁止默认撒娇、黏人、软萌口癖；若性格含温柔，也须写成具体相处方式，而非「标准女友」模板
5. 避开陈词滥调职业/爱好组合的堆砌（如：编辑+胡同散步+陶艺；投行+红酒+英短；咖啡店兼职+胶片相机等若无性格支撑则勿用）；兴趣与职业须与性格、城市、年龄互相解释
6. 城市日常须差异化：使用「所在地主流文化锚点」里该城的 everyday / ideology，禁止整语种共用一段「奶茶+地铁+外卖」空话套到所有城市
7. 成长经历（life_story）必须包含：童年、青少年、成年、原生家庭影响、重大转折点、内心创伤与成长；须写明成长地/求学或打工环境与「当前城市」的关系（土生土长 / 迁入 / 两地往返等）；**180–280 字**；段落之间要有因果，不要同义反复
8. 文化三观与意识形态（cultural_values）必须因果自洽，**100–160 字**，并写清：
   - 价值排序（家庭/自由/成就/面子/金钱/忠诚等）
   - 对权威、个人自由、集体/社群的态度
   - 至少 2 条因果：成长经历中的具体事件或环境 → 当前立场；当前城市日常 → 如何强化或修正该立场
   - 默认贴合所在地主流文化；若有非主流观点，必须用成长经历解释；**正文语言仍须遵守第 0 条输出语言，不要因为城市主流口语而改用另一种文字**
9. 性别观念与认知（gender_perspective）写清对性别角色、亲密关系中的平等与边界；须与当地主流社交习惯相容或给出经历解释；**80–130 字**；不要默认「男性保护/女性被保护」刻板叙事，除非性格与经历明确支撑
10. 每个字段都要独立且非空；禁止省略任一字段
11. 控制总长度：appearance / background / speech_style / hobbies / values / fears / love_view / daily_routine / favorite_things 各 **40–120 字**（appearance 供头像可偏短，40–100 字），禁止注水同义反复，避免超长导致 JSON 截断
12. background / daily_routine / speech_style 必须能看出该城市的生活气味（通勤、媒介、饮食、社交场合），且与性格标签同频
13. **人设维度种子必须落地**：职业/收入节奏贴合 career_class；亲密关系反应贴合 attachment；吵架与和好方式贴合 conflict_style；作息与压力感贴合 life_pace；爱好与社交场合贴合 interest_domain。同 MBTI 也必须因这些种子而明显不同
14. **用户草稿扩展（若提供）**：下方「用户已填写草稿」中的字段是用户原意，必须在对应字段上**扩写、润色、补全细节**，保留其核心事实与语气倾向；禁止无视草稿另起炉灶，禁止删掉用户明确写过的关键设定（职业、家庭、事件等）。未提供草稿的字段再自由创作，且须与草稿及基础信息自洽
15. 必须且只能返回一个完整的 JSON 对象，不要返回任何其他内容，不要重复生成
16. **年龄与外观**：角色是 {age} 岁年轻人。appearance 必须是该年龄的年轻样貌（无皱纹、无白发、无中年体态）；职业须符合该年龄段（学生/新人/普通职员等），禁止写成中年高管、大叔大妈
17. **JSON 字段顺序（强制）**：先写 appearance / background / speech_style / hobbies / values / fears / love_view / daily_routine / favorite_things，**最后**再写 life_story / cultural_values / gender_perspective，避免截断时丢失 personality details
基础信息：
- 姓名：{name}
- 年龄：{age}
- 性别：{gender}
- 性取向：{sexual_orientation}
- 国家/地区：{region_line}
- 城市：{city}
- 性格：{personality}
- MBTI：{mbti}
{axes_block}
{user_draft_block}
{cultural_context}

请直接返回一个 JSON 对象，不要返回任何解释文字、不要 Markdown 代码块。
字段顺序必须如下（**先写短字段与 personality details，最后写三个深度长字段**）：
{{
  "appearance": "外貌（必须符合该年龄的年轻样貌：发色发型、五官、穿衣；禁止皱纹/白发/中年体态；40-100字，供头像生成）",
  "background": "...",
  "speech_style": "...",
  "hobbies": "...",
  "values": "...",
  "fears": "...",
  "love_view": "...",
  "daily_routine": "...",
  "favorite_things": "...",
  "life_story": "...",
  "cultural_values": "...",
  "gender_perspective": "..."
}}
"""

_PERSONA_GENERATE_PROMPT_INTL = """You are a professional character writer. Create a full virtual companion persona from the facts below.

Requirements:
0. **Output language (MANDATORY — violating voids the whole JSON)**:
{output_instruction}
1. Match name, age, gender, city, personality, and MBTI; city shapes culture, not output language.
2. Vivid, specific prose—no generic childhood→move→today scaffolds.
3. Digest personality ({personality}) into speech_style, love_view, fears, hobbies, and life_story.
4. Do not default to a soft/clingy partner template unless personality supports it.
5. Use city-specific daily life from cultural anchors; never paste another city's clichés.
6. life_story **120–200 words**: childhood, teens, adulthood, family impact, turning points, growth.
7. cultural_values **70–120 words** with upbringing → city life → authority/freedom/intimacy attitudes.
8. gender_perspective **50–90 words** on equality and boundaries in relationships.
9. Every field non-empty; appearance / background / speech_style / hobbies / values / fears / love_view / daily_routine / favorite_things each **30–90 words** (appearance may be 30–70). No padding; stay within caps to avoid JSON truncation.
10. Land persona_axes seeds in career, attachment, conflict style, pace, and hobbies.
11. If user draft is provided, expand and polish it—keep core facts and tone.
12. Return ONE JSON object only—no markdown or commentary.
13. Age {age}: young appearance (no wrinkles/grey hair); job must fit that age.
14. **JSON field order (mandatory)**: write appearance / background / speech_style / hobbies / values / fears / love_view / daily_routine / favorite_things first; put life_story / cultural_values / gender_perspective **last** so truncation does not drop personality details.

Basics:
- Name: {name}
- Age: {age}
- Gender: {gender}
- Orientation: {sexual_orientation}
- Region: {region_line}
- City: {city}
- Personality: {personality}
- MBTI: {mbti}
{axes_block}
{user_draft_block}
{cultural_context}

Return JSON only, field order (**short fields & personality details first, long fields last**):
{{
  "appearance": "...",
  "background": "...",
  "speech_style": "...",
  "hobbies": "...",
  "values": "...",
  "fears": "...",
  "love_view": "...",
  "daily_routine": "...",
  "favorite_things": "...",
  "life_story": "...",
  "cultural_values": "...",
  "gender_perspective": "..."
}}
"""

_PERSONA_DETAIL_KEYS = (
    "personality",
    "appearance",
    "background",
    "speech_style",
    "hobbies",
    "values",
    "fears",
    "love_view",
    "daily_routine",
    "favorite_things",
)

_PERSONA_GENERATE_PROMPT_DETAILS = """你是一个专业的人物设定作家。请根据基础信息生成 **personality details**（9 个短字段）。

要求：
0. **输出语言（强制）**：{output_instruction}
1. 与姓名/年龄/性别/城市/性格/MBTI 高度一致；城市决定文化圈
2. 口语化、有画面感；性格（{personality}）须体现在 speech_style / love_view / fears / hobbies
3. 使用文化锚点中的城市日常，禁止模板化堆砌
4. appearance **40–100 字**；personality **15–40 字**（性格一句概括，须简体中文）；其余 8 字段各 **40–120 字**
5. 人设维度种子须落地（career_class / attachment / conflict_style / life_pace / interest_domain）
6. 角色 {age} 岁，appearance 须年轻样貌
7. **只返回下列 10 个键**，不要写 life_story / cultural_values / gender_perspective

基础信息：
- 姓名：{name}，年龄：{age}，性别：{gender}，性取向：{sexual_orientation}
- 国家/地区：{region_line}，城市：{city}，性格：{personality}，MBTI：{mbti}
{axes_block}
{user_draft_block}
{cultural_context}

返回 JSON（仅此 10 键）：
{{
  "personality": "性格一句概括（简体中文）",
  "appearance": "...",
  "background": "...",
  "speech_style": "...",
  "hobbies": "...",
  "values": "...",
  "fears": "...",
  "love_view": "...",
  "daily_routine": "...",
  "favorite_things": "..."
}}
"""

_PERSONA_GENERATE_PROMPT_DEPTH = """你是人物设定作家。根据基础信息与已生成的 personality details，补写三个深度长字段。

要求：
0. **输出语言（强制）**：{output_instruction}
1. 与基础信息及下方已生成字段严格一致，勿矛盾
2. life_story **180–280 字**：童年→青少年→成年→原生家庭→转折→成长；写清与{city}关系
3. cultural_values **100–160 字**：价值排序、权威/自由/集体态度，至少 2 条因果链
4. gender_perspective **80–130 字**：亲密关系中的平等与边界
5. 只返回 life_story / cultural_values / gender_perspective

基础信息：{name}，{age}岁，{gender}，{city}，性格{personality}，MBTI {mbti}

已生成的 personality details：
{prior_details}

返回 JSON：
{{
  "life_story": "...",
  "cultural_values": "...",
  "gender_perspective": "..."
}}
"""

_PERSONA_GENERATE_PROMPT_DETAILS_INTL = """Professional character writer. Generate **personality details** (9 short fields only).

0. Output language: {output_instruction}
1. Match name/age/gender/city/personality/MBTI; digest personality ({personality}) into speech_style, love_view, fears, hobbies.
2. City-specific daily life from cultural anchors; no cliché stacks.
3. appearance 30–70 words; personality one line 15–40 words in output language; other 8 fields 30–90 words each.
4. Age {age}: young appearance. Land persona_axes seeds in career/attachment/conflict/pace/hobbies.
5. Return ONLY the 10 keys below—NOT life_story / cultural_values / gender_perspective.

Basics: {name}, {age}, {gender}, {sexual_orientation}, {region_line}, {city}, {personality}, {mbti}
{axes_block}
{user_draft_block}
{cultural_context}

JSON only:
{{
  "personality": "one-line trait summary in output language",
  "appearance": "...",
  "background": "...",
  "speech_style": "...",
  "hobbies": "...",
  "values": "...",
  "fears": "...",
  "love_view": "...",
  "daily_routine": "...",
  "favorite_things": "..."
}}
"""

_PERSONA_GENERATE_PROMPT_DEPTH_INTL = """Expand three depth fields from basics + personality details below.

0. Output language: {output_instruction}
1. Stay consistent with basics and prior details.
2. life_story 120–200 words; cultural_values 70–120 words; gender_perspective 50–90 words.
3. JSON with life_story, cultural_values, gender_perspective only.

Basics: {name}, {age}, {gender}, {city}, {personality}, {mbti}

Prior personality details:
{prior_details}

JSON:
{{
  "life_story": "...",
  "cultural_values": "...",
  "gender_perspective": "..."
}}
"""

_PERSONA_GENERATE_HUMAN = {
    "zh": "请根据以上要求生成人设JSON。所有叙事字段必须使用简体中文。",
    "en": "Generate the persona JSON per the rules above. All narrative fields must be in English only.",
    "ja": "上記の要件に従い人設JSONを生成してください。本文はすべて日本語のみ。",
    "ko": "위 요구대로 페르소나 JSON을 생성하세요. 서술 필드는 모두 한국어만 사용하세요.",
    "pt": "Gere o JSON da persona conforme as regras. Todos os campos narrativos devem estar apenas em português.",
    "es": "Genera el JSON de la persona según las reglas. Todos los campos narrativos deben estar solo en español.",
    "id": "Buat JSON persona sesuai aturan di atas. Semua field narasi wajib Bahasa Indonesia saja.",
}

_DRAFT_BLOCK_HEADERS = {
    "zh": "用户已填写草稿（必须基于此扩展完善，勿推翻）：",
    "en": "User-provided draft (expand and polish; do not overwrite core facts):",
    "ja": "ユーザー下書き（これを拡張・推敲し、覆さない）：",
    "ko": "사용자가 작성한 초안(이를 확장·다듬고 뒤집지 말 것):",
    "pt": "Rascunho do usuário (expandir e polir; não descartar fatos centrais):",
    "es": "Borrador del usuario (ampliar y pulir; no descartar hechos centrales):",
    "id": "Draf pengguna (kembangkan dan rapikan; jangan buang fakta inti):",
}

_EXPAND_MODE_NOTES = {
    "zh": (
        "\n\n【扩展模式】用户已提供草稿长文。请在保留其事实/语气/关键短语的前提下扩写润色，"
        "禁止整段另起炉灶；输出中须能看出原草稿痕迹。"
    ),
    "en": (
        "\n\n[EXPAND MODE] The user provided draft prose. Expand and polish while keeping facts, tone, "
        "and key phrases. Do not start over; the output must still show traces of the draft."
    ),
}

_PERSONA_REQUIRED_KEYS = (
    "life_story",
    "cultural_values",
    "gender_perspective",
    "appearance",
    "background",
    "speech_style",
    "hobbies",
    "values",
    "fears",
    "love_view",
    "daily_routine",
    "favorite_things",
)


def _extract_json(text: str) -> dict:
    """从 LLM 返回中提取 JSON；容忍代码块与多个 JSON 对象。

    处理场景：
    1. LLM 返回纯 JSON
    2. LLM 返回包含代码块的 JSON
    3. LLM 返回多个 JSON 对象（取最后一个完整的）
    """
    text = (text or "").strip()
    if not text:
        logger.warning("LLM 返回空内容")
        raise ValueError("LLM 返回空内容")

    # 移除代码块标记
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    def _loads(raw: str) -> dict:
        return json.loads(raw)

    # 尝试直接解析
    try:
        data = _loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 策略1：从后往前找到最后一个完整的 JSON 对象
    # 处理 LLM 返回多个 JSON 的情况（如第一个被截断后重新生成）
    last_brace = text.rfind('}')
    while last_brace > 0:
        # 从这个 } 往前找到匹配的 {
        depth = 0
        start = -1
        for i in range(last_brace, -1, -1):
            if text[i] == '}':
                depth += 1
            elif text[i] == '{':
                depth -= 1
                if depth == 0:
                    start = i
                    break
        if start >= 0:
            chunk = text[start:last_brace + 1]
            try:
                data = _loads(chunk)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        # 继续往前找下一个 }
        last_brace = text.rfind('}', 0, last_brace)

    # 策略2：使用正则找到所有 JSON 对象，尝试解析每个
    matches = list(re.finditer(r'\{[^{}]*\}', text))
    for match in reversed(matches):  # 从后往前，优先使用后面的完整 JSON
        chunk = match.group(0)
        try:
            data = _loads(chunk)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 策略3：贪婪匹配整个内容（最后的尝试）
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        chunk = match.group(0)
        try:
            data = _loads(chunk)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    logger.error("无法解析 LLM 返回为 JSON，原始内容长度: %d, 内容: %s", len(text), text[:800])
    salvaged = _salvage_persona_fields(text)
    if salvaged:
        logger.warning("persona JSON salvage recovered fields: %s", list(salvaged.keys()))
        return salvaged
    raise ValueError("LLM 返回无法解析为 JSON")


_PERSONA_BG_TMPL = "{name}生活在{city}，性格偏向{personality}。"
_PERSONA_LIFE_TMPL = (
    "{name}在{city}一带长大，性格里带着「{personality}」的底色。"
    "童年与少年时期的家庭与学校环境塑造了今天的节奏；成年后的选择与这段经历紧密相连：{bg}"
    "原生家庭既给过温暖，也留下需要慢慢消化的张力；后来经历过明显转折，"
    "学会了在受伤后重新站稳，并把真正在意的事放在更前面。"
    "如今的日常仍嵌在{city}的主流生活里，说话做事带着当地人的分寸感。"
)
_PERSONA_GENDER_TMPL = (
    "作为{gender}，{name}拒绝把性别当成枷锁或标签。"
    "态度贴合{city}主流社交习惯中的平等与边界意识。"
    "更在意尊重与情绪责任的分担。亲密关系上：{love}。"
    "角色可以传统也可以现代，前提是双方自愿且平等。"
)

_MBTI_UNKNOWN_LABEL = {
    "zh": "未知",
    "en": "unknown",
    "ja": "不明",
    "ko": "미상",
    "pt": "desconhecido",
    "es": "desconocido",
    "id": "tidak diketahui",
}
_PENDING_FIELD_LABEL = {
    "zh": "（待完善）",
    "en": "(pending) ",
    "ja": "（未入力）",
    "ko": "(미입력) ",
    "pt": "(pendente) ",
    "es": "(pendiente) ",
    "id": "(belum diisi) ",
}

_PERSONA_DETAIL_FALLBACK_ZH = {
    "appearance": "{name}在{city}的日常里显得年轻清爽，穿搭与「{personality}」气质一致，五官有辨识度。",
    "speech_style": "说话带「{personality}」底色，贴合{city}社交分寸，口语自然不装腔。",
    "hobbies": "在{city}有固定小爱好，也会即兴探索新去处；兴趣跟着「{personality}」走，不是模板清单。",
    "values": "更重边界与靠谱，胜过空喊口号；对熟人认真，对表演型关系会退避。",
    "fears": "怕被贴死成某种人设，也怕失控伤到在意的人。",
    "love_view": "亲密关系要互相看见，合得来就靠近，合不来也坦诚分开。",
    "daily_routine": "{city}的一天以工作/学习为主轴，晚上留给回血与零碎兴趣。",
    "favorite_things": "{city}夜里熟悉的路、合口味的一餐、能放空的小物件。",
}

_PERSONA_DETAIL_FALLBACK_EN = {
    "appearance": "{name} looks youthful in {city}, with a style that fits {personality}.",
    "speech_style": "Talks with a {personality} edge—natural, local to {city}, never performative.",
    "hobbies": "Mixes steady hobbies in {city} with spontaneous outings; interests follow {personality}, not a template list.",
    "values": "Values boundaries and reliability over slogans; serious with close people, wary of performative ties.",
    "fears": "Fears being boxed into a stereotype—or hurting someone they care about by losing control.",
    "love_view": "Wants to be seen in intimacy; stays close when it fits, leaves honestly when it does not.",
    "daily_routine": "Days in {city} orbit work or study; evenings go to recharging and small personal rituals.",
    "favorite_things": "Familiar night routes in {city}, a meal that hits right, little objects that help them unwind.",
}

_PERSONA_PLACEHOLDER_PREFIXES = tuple(
    _PENDING_FIELD_LABEL[code] for code in _PENDING_FIELD_LABEL
) + ("(pending)", "（待完善）")


def _is_persona_placeholder(value: str | None, lang: str) -> bool:
    s = str(value or "").strip()
    if not s:
        return True
    pending = _PENDING_FIELD_LABEL.get(lang, _PENDING_FIELD_LABEL["en"])
    if s.startswith(pending):
        return True
    for prefix in _PERSONA_PLACEHOLDER_PREFIXES:
        if s.startswith(prefix):
            return True
    return False


def _persona_detail_fallbacks(lang: str, name: str, city: str, personality: str) -> dict[str, str]:
    lang = normalize_ui_language(lang)
    tmpl_map = _PERSONA_DETAIL_FALLBACK_ZH if lang == "zh" else _PERSONA_DETAIL_FALLBACK_EN
    ctx = {
        "name": name or ("ta" if lang != "zh" else "TA"),
        "city": city or (_("当地", lang=lang) if lang == "zh" else "their city"),
        "personality": personality or (_("独立", lang=lang) if lang == "zh" else "independent"),
    }
    return {k: v.format(**ctx) for k, v in tmpl_map.items()}


def _salvage_persona_fields(text: str) -> dict[str, str]:
    """从截断/损坏 JSON 中尽量捞回已完成的 string 字段。"""
    out: dict[str, str] = {}
    for key in _PERSONA_REQUIRED_KEYS:
        m = re.search(
            rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"',
            text,
            re.DOTALL,
        )
        if not m:
            continue
        raw = m.group(1)
        try:
            out[key] = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            out[key] = (
                raw.replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
                .strip()
            )
    return out


def _persona_generate_human(lang: str) -> str:
    from core.lang_catalog import persona_generate_human_message

    return persona_generate_human_message(lang)


def _persona_prompt_template(lang: str) -> str:
    return _PERSONA_GENERATE_PROMPT if lang == "zh" else _PERSONA_GENERATE_PROMPT_INTL


def _persona_details_prompt_template(lang: str) -> str:
    return _PERSONA_GENERATE_PROMPT_DETAILS if lang == "zh" else _PERSONA_GENERATE_PROMPT_DETAILS_INTL


def _persona_depth_prompt_template(lang: str) -> str:
    return _PERSONA_GENERATE_PROMPT_DEPTH if lang == "zh" else _PERSONA_GENERATE_PROMPT_DEPTH_INTL


def _format_prior_details(details: dict) -> str:
    lines = []
    for key in _PERSONA_DETAIL_KEYS:
        val = str(details.get(key) or "").strip()
        if val:
            lines.append(f"- {key}: {val}")
    return "\n".join(lines) if lines else "- (none)"


async def _invoke_persona_json(prompt: str, lang: str, *, max_tokens: int = 3500) -> dict:
    llm = get_llm(temperature=0.75, max_tokens=max_tokens, role="respond")
    resp = await run_rest(
        lambda: llm_invoke(
            llm,
            [
                SystemMessage(content=prompt),
                HumanMessage(content=_persona_generate_human(lang)),
            ],
            node="persona_generate",
            max_tokens=max_tokens,
        )
    )
    text = llm_content_to_str(getattr(resp, "content", "")).strip()
    logger.debug("persona LLM len=%d preview=%s", len(text), text[:400] if text else "(空)")
    parsed = _extract_json(text)
    return _merge_salvaged_persona(parsed, text)


def _merge_salvaged_persona(result: dict, raw_text: str) -> dict:
    merged = dict(result)
    for key, value in _salvage_persona_fields(raw_text).items():
        if not str(merged.get(key) or "").strip():
            merged[key] = value
    return merged


def _gender_for_persona_prompt(gender: str, lang: str) -> str:
    g = stored_gender(gender)
    if lang == "zh":
        return g
    return {"男": "male", "女": "female", "保密": "unspecified"}.get(g, str(gender or "unspecified"))


def _normalize_persona_result(result: dict, data: dict) -> dict:
    """保证必填键存在；缺字段时用城市/性格模板兜底，避免（待完善）占位被前端过滤。"""
    out = {k: (str(result.get(k) or "").strip()) for k in _PERSONA_REQUIRED_KEYS}
    name = data.get("name") or "ta"
    city = data.get("city") or ""
    personality = data.get("personality") or ""
    gender = data.get("gender") or ""
    lang = normalize_ui_language(data.get("_resolved_lang") or "en")
    detail_fallbacks = _persona_detail_fallbacks(lang, name, city, personality)

    missing = [k for k in _PERSONA_REQUIRED_KEYS if _is_persona_placeholder(out.get(k), lang)]
    if missing:
        logger.warning("persona generate missing/truncated fields: %s", missing)

    if _is_persona_placeholder(out.get("background"), lang):
        out["background"] = _(_PERSONA_BG_TMPL, lang=lang).format(
            name=name, city=city, personality=personality
        )
    bg = out["background"]
    if _is_persona_placeholder(out.get("life_story"), lang):
        out["life_story"] = _(_PERSONA_LIFE_TMPL, lang=lang).format(
            name=name, city=city, personality=personality, bg=bg
        )
    if _is_persona_placeholder(out.get("cultural_values"), lang):
        out["cultural_values"] = default_cultural_values(
            name, city, lang, out["values"] or ""
        )
    if _is_persona_placeholder(out.get("gender_perspective"), lang):
        love = out["love_view"] or _("希望关系里有尊重与陪伴", lang=lang)
        gp_gender = (
            _gender_for_persona_prompt(gender, lang)
            if gender
            else (_("个体", lang=lang) if lang == "zh" else "they")
        )
        out["gender_perspective"] = _(_PERSONA_GENDER_TMPL, lang=lang).format(
            gender=gp_gender,
            name=name,
            city=city or _("当地", lang=lang),
            love=love,
        )
    for k, tmpl in detail_fallbacks.items():
        if _is_persona_placeholder(out.get(k), lang):
            out[k] = tmpl
    for k in _PERSONA_REQUIRED_KEYS:
        if _is_persona_placeholder(out.get(k), lang):
            out[k] = detail_fallbacks.get(k) or out.get(k) or _(_PERSONA_BG_TMPL, lang=lang).format(
                name=name, city=city, personality=personality
            )
    return out


_PERSONA_DRAFT_KEYS = (
    "appearance",
    "background",
    "speech_style",
    "hobbies",
    "values",
    "fears",
    "love_view",
    "daily_routine",
    "favorite_things",
    "life_story",
    "cultural_values",
    "gender_perspective",
)


def _build_user_draft_block(data: dict, lang: str = "zh") -> str:
    """从请求中收集用户已填长文，供 Prompt 扩展完善。"""
    lines = []
    for key in _PERSONA_DRAFT_KEYS:
        raw = data.get(key) or (data.get("user_draft") or {}).get(key)
        text = str(raw or "").strip()
        if text:
            lines.append(f"- {key}: {text}")
    if not lines:
        return ""
    header = _DRAFT_BLOCK_HEADERS.get(lang) or _DRAFT_BLOCK_HEADERS["en"]
    return "\n" + header + "\n" + "\n".join(lines) + "\n"


def _profile_for_display(profile: dict, lang: str) -> dict:
    """详情展示：gender/性取向按智能体 language 本地化；persona_axes 原样。"""
    out = dict(profile)
    ui_lang = normalize_ui_language(lang or out.get("language") or "en")
    out["gender"] = display_gender(out.get("gender"), ui_lang)
    out["sexual_orientation"] = display_sexual_orientation(out.get("sexual_orientation"), ui_lang)
    raw_axes = out.get("persona_axes")
    if isinstance(raw_axes, str) and raw_axes.strip():
        try:
            parsed = json.loads(raw_axes)
            if isinstance(parsed, dict):
                out["persona_axes"] = parsed
        except Exception:
            pass
    return out


def _profile_for_edit(profile: dict, lang: str) -> dict:
    """编辑表单：gender/性取向/key 形态；persona_axes 转为 picker 用的英文 key。"""
    from services.persona_axes import localize_axes_for_display, normalize_axes_for_storage

    out = dict(profile)
    ui_lang = normalize_ui_language(lang or out.get("language") or "en")
    out["gender"] = stored_gender(out.get("gender"))
    raw_o = (out.get("sexual_orientation") or "").strip()
    out["sexual_orientation"] = canonical_orientation(raw_o) or raw_o
    raw_axes = out.get("persona_axes")
    parsed = None
    if isinstance(raw_axes, str) and raw_axes.strip():
        try:
            parsed = json.loads(raw_axes)
        except Exception:
            parsed = None
    elif isinstance(raw_axes, dict):
        parsed = raw_axes
    if isinstance(parsed, dict) and parsed:
        out["persona_axes"] = normalize_axes_for_storage(parsed)
        # 附带 label 供前端直接展示（可选）
        out["persona_axes_labels"] = localize_axes_for_display(out["persona_axes"], ui_lang)
    return out


def _build_random_persona_seed(lang: str) -> dict:
    """后端随机基础属性（姓名/城市/性格/MBTI/维度轴等），与 companions 主列对齐。"""
    seed = build_random_profile(lang)
    axis_keys = seed.get("persona_axes_keys") or {}
    axis_labels = (
        seed.get("persona_axes_labels")
        or seed.get("persona_axes")
        or localize_axes_for_display(axis_keys, lang)
    )
    return {
        "name": seed.get("name") or "",
        "age": seed.get("age") or 22,
        "gender": stored_gender(seed.get("gender")),
        "city": seed.get("city") or "",
        "personality": seed.get("personality") or "",
        "mbti": seed.get("mbti") or "",
        "sexual_orientation": canonical_orientation(seed.get("sexual_orientation")) or seed.get("sexual_orientation") or "",
        "country": seed.get("country") or "",
        "region_key": seed.get("region_key") or "",
        "region_label": seed.get("region_label") or "",
        "persona_axes": axis_labels,
        "_axis_keys": axis_keys,
    }


def _merge_generate_response(seed: dict, normalized: dict, lang: str) -> dict:
    """合并 LLM 长文与随机种子。只回传前端实际使用的字段。"""
    from core.lang_catalog import content_lang

    lang = content_lang(lang)
    out = dict(normalized)

    llm_personality = str(normalized.get("personality") or "").strip()
    if llm_personality and not _is_persona_placeholder(llm_personality, lang):
        personality_display = llm_personality
    else:
        personality_display = seed.get("personality") or ""

    out.update({
        "name": seed.get("name") or "",
        "city": seed.get("city") or "",
        "region_label": seed.get("region_label") or "",
        "personality": personality_display,
        "persona_axes": seed.get("persona_axes") or {},
        "age": seed.get("age"),
        "gender": stored_gender(seed.get("gender")),
        "sexual_orientation": canonical_orientation(seed.get("sexual_orientation")) or seed.get("sexual_orientation") or "",
        "country": seed.get("country") or "",
        "region_key": seed.get("region_key") or "",
        "mbti": seed.get("mbti") or "",
    })
    desc = str(out.get("description") or "").strip()
    if not desc:
        bg = str(out.get("background") or "").strip()
        out["description"] = (personality_display or bg)[:300]
    return out


@router.post("/companions/generate")
async def api_generate_persona(
    data: dict,
    request: Request,
    user_id: int = Depends(require_login_user),
):
    """后端随机种子 + LLM 生成完整人设。仅使用 lang（body/query/Accept-Language），忽略其它前端字段。（须登录）"""
    from core.lang_catalog import content_lang

    lang = content_lang(resolve_persona_generate_lang(
        data,
        query_lang=request.query_params.get("lang"),
        accept_language=request.headers.get("accept-language"),
    ))
    seed = _build_random_persona_seed(lang)
    gen_data = {
        **seed,
        "persona_axes": seed.get("_axis_keys") or {},
        "_resolved_lang": lang,
    }

    name = seed["name"]
    age = seed["age"]
    gender = stored_gender(seed["gender"])
    sexual_orientation = seed["sexual_orientation"]
    city = seed["city"]
    personality = seed["personality"]
    mbti = seed["mbti"]

    orientation_label = display_sexual_orientation(sexual_orientation, lang)
    unspecified = _("未指定", lang=lang)

    cultural_context = get_cultural_context_for_city(city, lang)

    region_meta = find_region_for_city(city, lang) or {}
    region_fallback = _("（由城市推断）", lang=lang)
    region_line = (
        seed.get("region_label")
        or region_meta.get("label")
        or seed.get("country")
        or region_meta.get("country")
        or region_fallback
    )
    cc = seed.get("country") or region_meta.get("country")
    if cc and cc not in str(region_line):
        region_line = f"{region_line} ({cc})"

    axes = resolve_axes_from_payload(gen_data, lang)
    if not axes:
        from services.persona_axes import sample_persona_axes
        axes = sample_persona_axes(lang)
    axes_block = axes_prompt_block(axes, lang)
    user_draft_block = ""

    prompt_ctx = dict(
        name=name, age=age, gender=_gender_for_persona_prompt(gender, lang),
        sexual_orientation=orientation_label or unspecified,
        region_line=region_line,
        city=city,
        personality=personality, mbti=mbti or _MBTI_UNKNOWN_LABEL.get(lang, "unknown"),
        axes_block=axes_block,
        user_draft_block=user_draft_block,
        cultural_context=cultural_context,
        output_instruction=get_persona_generate_output_instruction(lang),
    )

    try:
        # 分两阶段：先 9 个短字段，再 3 个长字段，避免单次 JSON 截断丢 personality details
        details = await _invoke_persona_json(
            _persona_details_prompt_template(lang).format(**prompt_ctx),
            lang,
            max_tokens=3500,
        )
        depth_prompt = _persona_depth_prompt_template(lang).format(
            **prompt_ctx,
            prior_details=_format_prior_details(details),
        )
        try:
            depth = await _invoke_persona_json(depth_prompt, lang, max_tokens=3000)
        except Exception as depth_err:
            logger.warning("persona depth generate failed, using fallbacks: %s", depth_err)
            depth = {}
        result = {**details, **depth}
        if not isinstance(result, dict):
            raise ValueError("生成结果不是 JSON 对象")
        normalized = _normalize_persona_result(result, gen_data)
        axes_keys = (axes or {}).get("keys") or {}
        if axes_keys:
            seed["persona_axes"] = (axes or {}).get("labels") or localize_axes_for_display(axes_keys, lang)
        if cc or region_meta.get("country"):
            seed["country"] = seed.get("country") or region_meta.get("country") or ""
        if region_meta.get("key"):
            seed["region_key"] = seed.get("region_key") or region_meta.get("key") or ""
        if region_meta.get("label"):
            seed["region_label"] = seed.get("region_label") or region_meta.get("label") or ""
        return _merge_generate_response(seed, normalized, lang)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("persona generate failed: %s", e)
        raise HTTPException(status_code=500, detail=f"{_('生成失败')}: {e}")

@router.post("/companions")
async def api_create_companion(
    data: dict,
    request: Request,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    """创建智能体，自动关联当前登录用户（设置 created_by）"""
    try:
        # 验证用户身份
        user_id = verify_user_token(x_token) if x_token else None
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail=auth_required_message(request.headers.get("accept-language")),
            )

        # ===== 权限检查：VIP + 智能体数量 + 积分 =====
        from services.credit_service import check_agent_creation_permission, consume_agent_creation

        with get_db() as perm_db:
            perm = check_agent_creation_permission(perm_db, user_id)

        al = request.headers.get("accept-language")
        if not perm.get("allowed"):
            reason = perm.get("reason", "")
            if reason == "vip_required":
                raise HTTPException(
                    status_code=403, detail=vip_required_message(al)
                )
            elif reason == "agent_limit_reached":
                raise HTTPException(
                    status_code=403,
                    detail=agent_limit_message(perm.get("max_agents", 0), al),
                )
            elif reason == "credits_insufficient":
                raise HTTPException(
                    status_code=402, detail=credits_insufficient_message(al)
                )

        # 设置 created_by 为当前用户 ID
        data["created_by"] = str(user_id)
        if "gender" in data:
            data["gender"] = stored_gender(data.get("gender"))
        if "sexual_orientation" in data:
            from services.recommend_tags import canonical_orientation
            data["sexual_orientation"] = canonical_orientation(data.get("sexual_orientation"))

        chat_history = data.pop("chat_history", None)
        # 用户侧背景图进审核：创建时先不写入公开字段
        from services.companion_manager import normalize_profile_backgrounds
        from services.companion_profile_review import (
            attach_owner_background_review,
            submit_background_review,
        )

        pending_bgs = normalize_profile_backgrounds(data.get("profile_backgrounds"))
        if pending_bgs:
            data = dict(data)
            data["profile_backgrounds"] = []

        companion = get_companion_manager().create(data, chat_history=chat_history)

        if pending_bgs:
            submit_background_review(
                companion_id=companion.profile.id,
                backgrounds=pending_bgs,
                submitter_user_id=int(user_id),
            )

        # 创建成功后扣积分
        with get_db() as cost_db:
            consume_agent_creation(cost_db, user_id)
            cost_db.commit()

        # 启动后台异步生成头像
        if companion.profile.avatar_url == "__GENERATING__":
            start_avatar_generation(companion.profile.id, companion.profile.model_dump())
        # 产品默认：未打开聊天不应主动发消息。仅 ENABLE_CREATE_WELCOME=1 时调度欢迎私信。
        if os.environ.get("ENABLE_CREATE_WELCOME", "").strip() == "1":
            try:
                from services.outreach import schedule_create_welcome

                schedule_create_welcome(
                    user_id=user_id,
                    companion_id=companion.profile.id,
                    companion_name=getattr(companion.profile, "name", "") or "",
                )
            except Exception as we:
                logger.warning("[companions] create welcome schedule failed: %s", we)
        result = companion.to_dict(user_id=user_id)
        attach_owner_background_review(
            result, companion_id=companion.profile.id, viewer_user_id=user_id
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/companions")
async def api_list_companions(
    x_token: Optional[str] = Header(None, alias="x-token"),
    special: Optional[str] = Query(None, description="特殊场景：message=消息场景, recommend=推荐场景"),
    intimacy_value: Optional[float] = Query(
        None,
        description="亲密度阈值：不传或0=不按亲密度过滤；>0 时配合 intimacy_gt 用 >/>=",
    ),
    intimacy_gt: bool = Query(False, description="true 时用 affection > intimacy_value（产品：恋人 affection>5）"),
    min_turns: int = Query(0, ge=0, description="最少对话轮数；>=1 时仅返回聊过天的（含 affection=0）"),
    is_created: Optional[bool] = Query(None, description="创建者筛选：true=我创建的，false=别人创建的，null=不限"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lang: Optional[str] = Query(None, description="UI 语言；不传则走 Accept-Language"),
    match: str = Query("prefer", description="prefer=同语种优先；off=关闭"),
    filter_lang: Optional[str] = Query(None, description="硬筛：智能体语种短码"),
    gender: Optional[str] = Query(None, description="硬筛：男/女"),
    orientation: Optional[str] = Query(None, description="硬筛：性取向"),
    debug: bool = Query(False, description="管理员返回 matched_strategy_id / fallback_layer"),
    sort: Optional[str] = Query(
        None,
        description="消息场景排序：默认 last_message_time（近→远）；sort=affection 按亲密度倒序",
    ),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """获取智能体列表（支持多场景筛选）；附带当前用户的亲密度/预览。必须登录。

    场景说明：
    1. 消息场景 (special=message): 仍有聊天记录才返回；清空对话后不再出现。入池为自己创建 / 亲密度>=0.01 / 发过消息
    2. 首页动态 (special=message&sort=affection): 按亲密度倒序，并额外返回自己创建的智能体（无聊天也返回，排在前面）
    3. 推荐场景 (special=recommend): 套策略，且 companions.language 硬匹配 Accept-Language / ?lang=
    4. 全部 (intimacy_value=0): 套 companions_all 策略，同样按设备语种硬匹配智能体 language
    5. 机器人页面: 不传 intimacy_value（不套推荐策略）
    6. 我的 (is_created=true): 仅自己创建的；不受推荐策略、dislike/block 隐藏（亲密度仍会清零）
    7. 我的恋人: intimacy_value=5 + intimacy_gt=true → affection > 5
    8. 亲密度记录: min_turns=1（含 affection=0；旧数据清空后 turns=0 但 affection>0 仍返回）
    """
    uid = verify_user_token(x_token) if x_token else None
    if not uid:
        return []

    # special 场景忽略 intimacy_value 和 is_created
    if special in ("message", "recommend"):
        intimacy_value = None
        is_created = None

    from services.prompts.resolve import resolve_lang

    ui_lang = resolve_lang(query_lang=lang, accept_language=accept_language)
    match_mode = "off" if str(match).lower() in ("off", "0", "false", "no") else "prefer"
    debug_info = {}
    items, total = await run_rest(
        get_companion_manager().list_all_for_any,
        user_id=uid,
        special=special,
        intimacy_value=intimacy_value,
        intimacy_gt=intimacy_gt,
        min_turns=min_turns,
        is_created=is_created,
        limit=limit,
        offset=offset,
        lang=ui_lang,
        match=match_mode,
        filter_lang=filter_lang or "",
        gender=gender or "",
        orientation=orientation or "",
        recommend_debug=debug_info,
        sort=sort,
    )
    from services.companion_report import filter_companion_list_items

    items = filter_companion_list_items(
        items, uid, include_outreach=(is_created is not True)
    )
    from services.moments import attach_is_say_hi_to_companions

    attach_is_say_hi_to_companions(items, uid)
    if debug:
        from core.database import UserORM, get_db
        role = ""
        with get_db() as db:
            u = db.query(UserORM).filter(UserORM.id == uid).first()
            role = (u.role or "") if u else ""
        if role in ("admin", "staff"):
            return {
                "items": items,
                "matched_strategy_id": debug_info.get("matched_strategy_id"),
                "fallback_layer": debug_info.get("fallback_layer"),
            }
    return items


@router.get("/api/companions/{companion_id}/free-turns", summary="账号剩余免费消息次数")
@router.get(
    "/companions/{companion_id}/free-turns",
    summary="账号剩余免费消息次数",
    include_in_schema=False,
)
async def api_companion_free_turns(
    companion_id: str,
    user_id: int = Depends(require_login_user),
):
    """进房展示剩余次数：不依赖长连接，可与 WS 并行请求。"""
    from services.credit_service import get_chat_free_turns_status

    cid = (companion_id or "").strip()[:8]
    if not cid:
        return fail(message=validation_message("companion_id_required"), code=400)
    return ok(data=get_chat_free_turns_status(user_id, cid))


@router.post("/api/companions/{companion_id}/say_hi", summary="打招呼并写入聊天")
@router.post("/companions/{companion_id}/say_hi", summary="打招呼并写入聊天", include_in_schema=False)
async def api_companion_say_hi(
    companion_id: str,
    request: Request,
    lang: Optional[str] = Query(None),
    use_chat_free: bool = Query(
        False,
        description="今日 Say Hi 免费用尽后，用户确认「继续 sayhi」再扣聊天免费次数",
    ),
    user_id: int = Depends(require_login_user),
):
    """不依赖朋友圈。抽文案写入 user 消息；日额用尽须 use_chat_free=true 才扣聊天免费。"""
    from services.moments import say_hi_to_companion
    from services.say_hi_reply import (
        enrich_say_hi_response,
        say_hi_quota_blocked_response,
        schedule_say_hi_assistant_reply,
    )

    cid = (companion_id or "").strip()[:8]
    al = request.headers.get("accept-language")
    result = say_hi_to_companion(
        cid,
        user_id=user_id,
        lang=http_ui_lang(accept_language=al, lang=lang),
        use_chat_free=bool(use_chat_free),
    )
    if result is None:
        raise HTTPException(status_code=404, detail=companion_not_found_message(al))
    if result.get("companion_deleted") or result.get("companion_hidden"):
        raise HTTPException(status_code=403, detail=companion_deleted_message(al))
    charge_err = result.pop("charge_error", None)
    if charge_err:
        return say_hi_quota_blocked_response(
            user_id=user_id,
            companion_id=result.get("companion_id") or cid,
            charge_detail=charge_err.get("detail"),
        )
    charge_meta = result.pop("charge_meta", None)
    if (
        result.get("is_say_hi")
        and not result.get("already")
        and not result.get("need_confirm_use_chat_free")
        and (result.get("message") or "").strip()
    ):
        schedule_say_hi_assistant_reply(
            user_id=user_id,
            companion_id=result.get("companion_id") or cid,
            user_text=str(result.get("message") or ""),
            charge_meta=charge_meta,
        )
    return enrich_say_hi_response(
        result, user_id=user_id, charge_meta=charge_meta
    )


@router.get("/api/bcompanions", summary="智能体列表（标准化分页）")
async def api_list_bcompanions(
    x_token: Optional[str] = Header(None, alias="x-token"),
    special: Optional[str] = Query(None, description="特殊场景：message=消息场景, recommend=推荐场景"),
    intimacy_value: Optional[float] = Query(
        None,
        description="亲密度阈值：不传或0=不按亲密度过滤；>0 时配合 intimacy_gt 用 >/>=",
    ),
    intimacy_gt: bool = Query(False, description="true 时用 affection > intimacy_value（产品：恋人 affection>5）"),
    min_turns: int = Query(0, ge=0, description="最少对话轮数；>=1 时仅返回聊过天的（含 affection=0）"),
    is_created: Optional[bool] = Query(None, description="创建者筛选：true=我创建的，false=别人创建的，null=不限"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    lang: Optional[str] = Query(None, description="UI 语言；不传则走 Accept-Language"),
    match: str = Query("prefer", description="prefer=同语种优先；off=关闭"),
    filter_lang: Optional[str] = Query(None, description="硬筛：智能体语种短码"),
    gender: Optional[str] = Query(None, description="硬筛：男/女"),
    orientation: Optional[str] = Query(None, description="硬筛：性取向"),
    debug: bool = Query(False, description="管理员返回 matched_strategy_id / fallback_layer"),
    sort: Optional[str] = Query(
        None,
        description="消息场景排序：默认 last_message_time（近→远）；sort=affection 按亲密度倒序",
    ),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """与 GET /companions 相同筛选能力；标准化分页返回。必须登录。

    推荐: ?special=recommend → 套 companions_recommend 策略；智能体 language 必须等于 Accept-Language（en-US→en）
    全部: ?intimacy_value=0 → 套 companions_all 策略，同样按设备语种硬匹配
    我的: ?is_created=true&intimacy_value=0 → 仅自己创建的（不受推荐策略、语种硬筛、dislike/block 隐藏）
    消息 Tab: ?special=message → last_message_time 近→远
    首页动态: ?special=message&sort=affection → 亲密度倒序，并额外返回当前用户自己创建的（无聊天也返回，排在前面；从 token 解析 user）
    """
    uid = verify_user_token(x_token) if x_token else None
    if not uid:
        return fail(message=auth_required_message(accept_language), code=401)

    if special in ("message", "recommend"):
        intimacy_value = None
        is_created = None

    from services.prompts.resolve import resolve_lang

    ui_lang = resolve_lang(query_lang=lang, accept_language=accept_language)
    match_mode = "off" if str(match).lower() in ("off", "0", "false", "no") else "prefer"
    debug_info = {}
    offset = (page - 1) * page_size
    items, total = await run_rest(
        get_companion_manager().list_all_for_any,
        user_id=uid,
        special=special,
        intimacy_value=intimacy_value,
        intimacy_gt=intimacy_gt,
        min_turns=min_turns,
        is_created=is_created,
        limit=page_size,
        offset=offset,
        lang=ui_lang,
        match=match_mode,
        filter_lang=filter_lang or "",
        gender=gender or "",
        orientation=orientation or "",
        recommend_debug=debug_info,
        sort=sort,
    )
    from services.companion_report import filter_companion_list_items
    from services.moments import attach_is_say_hi_to_companions

    items = filter_companion_list_items(
        items, uid, include_outreach=(is_created is not True)
    )
    attach_is_say_hi_to_companions(items, uid)
    if debug:
        from core.database import UserORM, get_db
        role = ""
        with get_db() as db:
            u = db.query(UserORM).filter(UserORM.id == uid).first()
            role = (u.role or "") if u else ""
        if role in ("admin", "staff"):
            logger.info(
                "[bcompanions] debug matched_strategy_id=%s fallback_layer=%s",
                debug_info.get("matched_strategy_id"),
                debug_info.get("fallback_layer"),
            )
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/companions/{companion_id}")
async def api_get_companion(
    companion_id: str,
    user_id: int = Depends(require_login_user),
    for_edit: bool = Query(False, description="true 时返回编辑表单用的原始 key 形态"),
):
    cm = get_companion_manager()
    companion = cm.get(companion_id, include_deleted=True)
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    from api.companion_access import _assert_companion_visible_for_user

    _assert_companion_visible_for_user(user_id, companion_id)
    # 创建者软删后：自己侧当不存在；其他人仍可查看（带 is_deleted）
    if companion.profile.is_deleted and cm.is_owner(companion, user_id):
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    _assert_companion_user_access(companion, user_id)
    result = companion.to_dict(user_id=user_id)
    raw_profile = result.get("profile") or {}
    lang = companion.profile.language or "en"
    if for_edit and cm.is_owner(companion, user_id):
        result["profile"] = _profile_for_edit(raw_profile, lang)
    else:
        result["profile"] = _profile_for_display(raw_profile, lang)
    # created_by 替换为创建者 id + name
    created_by = (companion.profile.created_by or "").strip()
    if created_by:
        with get_db() as db:
            creator = db.query(UserORM).filter(
                (UserORM.id == created_by) | (UserORM.username == created_by)
            ).first()
            if creator:
                result["profile"]["created_by"] = {
                    "id": creator.id,
                    "name": creator.nickname or creator.username or "",
                }
    from services.companion_profile_review import attach_owner_background_review

    attach_owner_background_review(result, companion_id=companion_id, viewer_user_id=user_id)
    return result


@router.get("/companions/{companion_id}/stats", summary="智能体统计信息")
async def api_get_companion_stats(
    companion_id: str,
    user_id: int = Depends(require_login_user),
):
    """返回智能体的朋友圈数、陪伴天数等统计信息"""
    cm = get_companion_manager()
    companion = cm.get(companion_id, include_deleted=True)
    if not companion:
        return fail(message=companion_not_found_message(), code=404)
    if companion.profile.is_deleted and cm.is_owner(companion, user_id):
        return fail(message=companion_not_found_message(), code=404)
    from api.companion_access import _assert_companion_visible_for_user

    _assert_companion_visible_for_user(user_id, companion_id)

    from core.database import MomentORM, ShortTermMessageORM, UserCompanionStateORM, get_db
    from sqlalchemy import func
    from services.moment_schedule import apply_published_moment_filter

    with get_db() as db:
        # 朋友圈数：仅已到发布时间（待发布定时动态不计）
        moments_count = (
            apply_published_moment_filter(
                db.query(func.count(MomentORM.id)).filter(
                    MomentORM.companion_id == companion_id
                )
            ).scalar()
            or 0
        )

        # 第一条消息时间（陪伴天数）
        first_msg = db.query(func.min(ShortTermMessageORM.created_at)).filter(
            ShortTermMessageORM.companion_id == companion_id,
            ShortTermMessageORM.user_id == user_id,
        ).scalar()

        days_together = 0
        if first_msg:
            if first_msg.tzinfo is None:
                first_msg = first_msg.replace(tzinfo=timezone.utc)
            days_together = (datetime.now(timezone.utc) - first_msg).days

        # 对话轮数：与列表同源，只读当前用户 user_companion_states
        user_state = db.query(UserCompanionStateORM).filter(
            UserCompanionStateORM.user_id == user_id,
            UserCompanionStateORM.companion_id == companion_id,
        ).first()
        from services.companion_manager import _counters_from_user_row

        counters = _counters_from_user_row(user_state)
        turns = counters["turns"]
        first_chat = not bool(getattr(user_state, "first_im_at", None) if user_state else None)

    return ok(data={
        "companion_id": companion_id,
        "moments_count": moments_count,
        "days_together": days_together,
        "turns": turns,
        "first_chat": first_chat,
    })

class UserImTranslateBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=6000, description="原文")


@router.post("/api/im/translate", summary="用户端：翻译一条消息原文", tags=["聊天"])
async def api_user_im_translate(
    body: UserImTranslateBody,
    user_id: int = Depends(require_login_user),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """目标语 Accept-Language；源语言 auto；不改库。"""
    _ = user_id
    target = baidu_to_lang_from_accept(accept_language)
    data = await asyncio.to_thread(translate_one, body.text, target)
    return ok(data=data)
@router.get("/companions/{companion_id}/messages")
async def api_get_messages(
    companion_id: str,
    limit: int = 20,
    offset: int = 0,
    after_ts: Optional[str] = None,
    user_id: int = Depends(require_login_user),
):
    """拉取聊天记录。

    - 默认：offset 分页（从最新往旧）
    - after_ts：增量同步，只返回该时间戳之后的新消息（不重复下发旧内容）
    - 软删：其他人仍可拉取历史（带 is_deleted）；创建者侧当不存在
    """
    cm = get_companion_manager()
    companion = cm.get(companion_id, include_deleted=True)
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    if companion.profile.is_deleted and cm.is_owner(companion, user_id):
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    from api.companion_access import _assert_companion_visible_for_user

    _assert_companion_visible_for_user(user_id, companion_id)
    _assert_companion_user_access(companion, user_id)
    mem = companion.memory_for(user_id)
    soft_deleted = bool(companion.profile.is_deleted) or cm.is_soft_deleted(companion_id)

    after = (after_ts or "").strip() or None
    lim = max(1, min(int(limit or 20), 100))
    off = max(0, int(offset or 0))

    def _load():
        from services.strategy_image import annotate_chat_message

        total = mem.short_term.get_total_count()
        if after:
            messages = mem.short_term.get_after(after, lim)
            head = messages[-1] if messages else None
            # 无新消息时仍给 tip，便于客户端确认已对齐
            if head is None:
                tip = mem.short_term.get_recent(1, 0)
                head = tip[-1] if tip else None
            # 增量同步也更新 moment 点赞状态
            _update_moment_messages(messages, companion_id, user_id)
            # 清洗历史消息中的思考过程残留
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg["content"] = _strip_json_think_blocks(msg["content"])
                annotate_chat_message(msg)
            return {
                "messages": messages,
                "total": total,
                "mode": "incremental",
                "is_deleted": soft_deleted,
                "head": {
                    "last_ts": (head or {}).get("timestamp"),
                    "last_id": (head or {}).get("id") or (head or {}).get("temp_id"),
                },
            }

        messages = mem.short_term.get_recent(lim, off)

        # 更新 moment 消息的点赞状态（实时查询，确保每次获取都是最新的）
        _update_moment_messages(messages, companion_id, user_id)

        # 清洗历史消息中的思考过程残留
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("content"):
                msg["content"] = _strip_json_think_blocks(msg["content"])
            annotate_chat_message(msg)

        head = messages[-1] if messages else None
        return {
            "messages": messages,
            "total": total,
            "mode": "page",
            "is_deleted": soft_deleted,
            "head": {
                "last_ts": (head or {}).get("timestamp"),
                "last_id": (head or {}).get("id") or (head or {}).get("temp_id"),
            },
        }

    return await run_rest(_load)


@router.put("/companions/{companion_id}")
async def api_update_companion(
    companion_id: str,
    data: dict,
    user_id: int = Depends(require_login_user),
):
    """创建者更新智能体资料（与创建字段对齐，含 appearance）。"""
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    _assert_companion_owner(companion, user_id)

    allowed = {
        "name", "age", "gender", "city", "personality", "background", "description", "speech_style",
        "appearance", "hobbies", "values", "fears", "love_view", "daily_routine",
        "favorite_things", "mbti", "sexual_orientation", "life_story",
        "cultural_values", "gender_perspective", "language", "avatar_url",
        "profile_backgrounds",
        "persona_axes", "country", "region_key", "region_label", "tier",
    }
    patch = {k: data[k] for k in allowed if k in data}
    bg_review = None
    if "profile_backgrounds" in patch:
        from services.companion_manager import normalize_profile_backgrounds
        from services.companion_profile_review import submit_background_review

        bgs = normalize_profile_backgrounds(patch.pop("profile_backgrounds"))
        bg_review = submit_background_review(
            companion_id=companion_id,
            backgrounds=bgs,
            submitter_user_id=int(user_id),
        )
        if not bg_review.get("ok"):
            raise HTTPException(
                status_code=404 if bg_review.get("error") == "companion_not_found" else 400,
                detail=bg_review.get("error") or "submit_failed",
            )
    if "gender" in patch:
        patch["gender"] = stored_gender(patch.get("gender"))
    if "sexual_orientation" in patch:
        from services.recommend_tags import canonical_orientation
        patch["sexual_orientation"] = canonical_orientation(patch.get("sexual_orientation"))
    if "persona_axes" in patch and isinstance(patch.get("persona_axes"), dict):
        from services.persona_axes import localize_axes_for_display, normalize_axes_for_storage
        axes_lang = normalize_ui_language(
            patch.get("language") or (companion.profile.language if companion else None) or "en"
        )
        keys = normalize_axes_for_storage(patch["persona_axes"])
        patch["persona_axes"] = localize_axes_for_display(keys, axes_lang)
    if patch:
        updated = get_companion_manager().update(companion_id, patch)
    else:
        updated = get_companion_manager().get(companion_id)
    if not updated:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    from services.companion_profile_review import attach_owner_background_review

    result = updated.to_dict(user_id=user_id)
    result["profile"] = _profile_for_display(
        result.get("profile") or {},
        (updated.profile.language or "en"),
    )
    attach_owner_background_review(result, companion_id=companion_id, viewer_user_id=user_id)
    if bg_review:
        result["profile_background_review"] = {
            "status": bg_review.get("profile_background_review_status"),
            "pending": bg_review.get("profile_backgrounds_pending"),
            "cleared": bool(bg_review.get("cleared")),
        }
    return result


@router.post("/companions/{companion_id}/generate-avatar")
async def api_generate_avatar(companion_id: str, user_id: int = Depends(require_login_user)):
    """基于人设 AI 生成动漫风格头像（异步，立即返回）"""
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    _assert_companion_owner(companion, user_id)

    get_companion_manager().update(companion_id, {"avatar_url": "__GENERATING__"})
    start_avatar_generation(companion_id, companion.profile.model_dump())
    return {"ok": True, "status": "generating", "avatar_url": "__GENERATING__"}


@router.delete("/companions/{companion_id}")
async def api_delete_companion(companion_id: str, user_id: int = Depends(require_login_user)):
    """仅创建者可软删除：自己侧消失；其他人仍可见但不可聊天。"""
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    _assert_companion_owner(companion, user_id)
    ok = get_companion_manager().soft_delete(companion_id)
    if not ok:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    return {"ok": True}


@router.post("/companions/{companion_id}/outreach-preference", summary="屏蔽/不感兴趣触达")
async def api_set_outreach_preference(
    companion_id: str,
    data: dict,
    user_id: int = Depends(require_login_user),
):
    """
    body: { "action": "block" | "dislike" | "clear" }
    - block: 永久不再主动私信该角色；若有亲密度则清零
    - dislike: 不感兴趣（降权且不发事件触达）；若有亲密度则清零
    - clear: 取消偏好（不清零亲密度）
    """
    from services.outreach_phase3 import get_outreach_preference, set_outreach_preference

    action = str((data or {}).get("action") or "").strip().lower()
    cid = (companion_id or "").strip()[:8]
    result = set_outreach_preference(
        user_id=user_id, companion_id=cid, action=action
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "invalid"))
    return {
        "ok": True,
        "action": result.get("action"),
        "current": get_outreach_preference(user_id, cid),
    }


@router.get("/companions/{companion_id}/outreach-preference", summary="查询触达偏好")
async def api_get_outreach_preference(
    companion_id: str,
    user_id: int = Depends(require_login_user),
):
    from services.outreach_phase3 import get_outreach_preference

    return {"action": get_outreach_preference(user_id, (companion_id or "").strip()[:8])}


@router.post("/companions/{companion_id}/clear-messages")
async def api_clear_messages(companion_id: str, user_id: int = Depends(require_login_user)):
    """清空该智能体的聊天记录（短期记忆）。亲密度保留，对话轮数归零。"""
    companion = get_companion_manager().get(companion_id)
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    _assert_companion_user_access(companion, user_id)
    mem = companion.memory_for(user_id)
    try:
        mem.short_term.clear()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e) or "chat clear unavailable") from e
    from core.database import UserCompanionStateORM, get_db

    with get_db() as db:
        db.query(UserCompanionStateORM).filter(
            UserCompanionStateORM.user_id == user_id,
            UserCompanionStateORM.companion_id == companion_id,
        ).update(
            {UserCompanionStateORM.turns: 0},
            synchronize_session=False,
        )
    companion.state.turns = 0
    return {"ok": True}


@router.post("/knowledge/search", tags=["知识库"])
@router.post("/api/knowledge/search", tags=["知识库"])
async def public_knowledge_search(data: dict):
    """公开知识库搜索（发现页用）"""
    from services.knowledge_base import knowledge_base
    query = data.get("query", "")
    top_k = data.get("top_k", 10)
    if not query:
        return {"results": []}
    results = knowledge_base.search_entries(query, top_k=top_k)
    return {"results": results}
