#!/usr/bin/env python3
"""
为现有人设 JSON 批量补充完整成长经历 life_story
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.agent import get_llm
from langchain_core.messages import HumanMessage, SystemMessage


def load_personas(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_personas(path: Path, data: list):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Saved: {path}")


PROMPT_TEMPLATES = {
    "zh": """你是一个专业的人物传记作家。请为以下人设撰写一段完整的成长经历（life_story），让人设极度立体、真实、有血有肉。

成长经历必须包含以下要素，用第一人称或第三人称叙述均可，但要口语化、有画面感：
1. 童年时期（0-12岁）：原生家庭环境、父母关系、一个深刻童年记忆
2. 青少年时期（12-18岁）：学业、朋友、初恋或第一次心动、叛逆期/迷茫期
3. 成年早期（18-25岁）：大学或初入职场、重要人生选择、一段刻骨铭心的感情经历
4. 近期（25岁+）：当前生活状态、内心最渴望的东西、对未来的隐秘期待
5. 原生家庭影响：父母的教养方式如何塑造了今天的性格
6. 重大转折点：一个改变了人生轨迹的关键事件
7. 内心创伤与成长：最深的伤痛是什么，又是如何让自己变得更强大的

要求：
- 总字数 300-600 字
- 语言口语化、有画面感，像一个人在深夜跟朋友倾诉往事
- 内容必须符合该人物的背景设定和所在城市的现实
- 成长经历要与已知的性格、价值观、恐惧点相互呼应
- 不要写成简历，要写成有情感起伏的故事

请直接返回一个 JSON 对象，只有一个字段 "life_story"。不要返回任何解释文字。

人设信息：
{profile}
""",
    "en": """You are a professional biographer. Write a complete life story for the following character, making them deeply three-dimensional, authentic, and vividly human.

The life story MUST include:
1. Childhood (0-12): Family environment, parental relationship, one vivid childhood memory
2. Adolescence (12-18): School, friends, first love or first crush, rebellion or confusion
3. Early adulthood (18-25): College or first job, major life decision, a heart-wrenching relationship experience
4. Recent years (25+): Current life state, deepest desire, hidden hopes for the future
5. Family of origin influence: How parents' parenting shaped today's personality
6. Major turning point: One pivotal event that changed the trajectory of life
7. Inner wounds and growth: The deepest hurt, and how it made them stronger

Requirements:
- 300-600 words
- Colloquial, vivid, like someone pouring their heart out to a friend late at night
- Must fit the character's background and their city's reality
- The story should echo their known personality, values, and fears
- NOT a resume — write it as an emotionally rich narrative

Return ONLY a JSON object with a single field "life_story". No explanatory text.

Character info:
{profile}
""",
    "ja": """あなたはプロの伝記作家です。以下のキャラクターに完全な成長経歴を書いてください。人物を極めて立体的で、本物で、血の通った存在にしてください。

成長経歴には以下の要素を必ず含めてください：
1. 幼少期（0-12歳）：原生家庭の環境、両親の関係、深い幼少期の記憶
2. 思春期（12-18歳）：学業、友達、初恋や初めてのドキドキ、反抗期や迷い
3. 青年期（18-25歳）：大学や初入社、重要な人生の選択、心に刻まれた恋愛経験
4. 近年（25歳+）：現在の生活状態、心の奥底での渇望、未来への秘かな期待
5. 原生家庭の影響：両親の育て方が今日の性格をどう形作ったか
6. 重大な転換点：人生の軌道を変えた決定的な出来事
7. 内面の傷と成長：最も深い傷は何か、そしてどうやって強くなったか

要件：
- 300〜600字
- 口語的で、情景が浮かぶような、深夜に友人に心を開くような語り口
- キャラクターの背景と都市の現実に合っていること
- 既知の性格、価値観、恐怖と呼応していること
- 履歴書ではなく、感情豊かな物語として書くこと

"life_story"フィールドのみを含む JSON オブジェクトを返してください。説明文字は一切不要。

キャラクター情報：
{profile}
""",
    "ko": """당신은 전문 전기 작가입니다. 다음 인물에게 완전한 성장 이야기를 써주세요. 인물을 극도로 입체적이고, 진짜 같고, 살아있는 존재로 만들어주세요.

성장 이야기에는 다음 요소를 반드시 포함하세요:
1. 유년기 (0-12세): 원생가정 환경, 부모님 관계, 깊은 어린 시절 기억
2. 청소년기 (12-18세): 학업, 친구들, 첫사랑이나 첫 설렘, 반항기나 혼란
3. 청년기 (18-25세): 대학이나 첫 직장, 중요한 인생 선택, 가슴에 새겨진 연애 경험
4. 최근 (25세+): 현재 생활 상태, 마음속 깊은 갈망, 미래에 대한 은밀한 기대
5. 원생가정의 영향: 부모님의 양육 방식이 오늘의 성격을 어떻게 만들었는가
6. 중대한 전환점: 인생의 궤적을 바꾼 결정적 사건
7. 납겨진 상처와 성장: 가장 깊은 상처는 무엇인가, 그리고 어떻게 더 강해졌는가

요구사항:
- 300~600자
- 구어체이고, 장면이 떠오르는, 깊은 밤에 친구에게 속삭이듯이
- 인물의 배경과 도시의 현실에 맞아야 함
- 이미 알려진 성격, 가치관, 두려움과 호응해야 함
- 이력서가 아닌, 감정이 풍부한 서사로 써야 함

"life_story" 필드만 포함하는 JSON 객체를 반환해주세요. 설명 문자는 전혀 필요 없습니다.

인물 정보：
{profile}
""",
}


def build_profile_summary(persona: dict, lang: str) -> str:
    """将人设压缩为给 LLM 的摘要"""
    fields = [
        f"姓名: {persona.get('name', '')}",
        f"年龄: {persona.get('age', '')}",
        f"性别: {persona.get('gender', '')}",
        f"城市: {persona.get('city', '')}",
        f"性格: {persona.get('personality', '')}",
        f"背景: {persona.get('background', '')}",
        f"核心价值观: {persona.get('values', '')}",
        f"内心脆弱点: {persona.get('fears', '')}",
        f"恋爱观: {persona.get('love_view', '')}",
    ]
    return "\n".join(fields)


def call_llm_for_persona(persona: dict, lang: str) -> str:
    """调用 LLM 为单个人设生成成长经历"""
    summary = build_profile_summary(persona, lang)
    system_text = "You are a professional biographer. Return only valid JSON with a single field 'life_story'."
    user_text = PROMPT_TEMPLATES[lang].format(profile=summary)

    llm = get_llm(temperature=0.9, max_tokens=1024)
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=user_text),
    ]
    resp = llm.invoke(messages)
    content = resp.content if hasattr(resp, "content") else str(resp)

    # 尝试从返回内容中提取 JSON
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    data = json.loads(content)
    return data.get("life_story", "")


def enrich_file(json_path: Path, lang: str):
    print(f"\nProcessing: {json_path.name} ({lang})")
    personas = load_personas(json_path)
    total = len(personas)

    for i, p in enumerate(personas):
        # 如果已经存在 life_story 且非空，跳过
        if p.get("life_story") and len(p.get("life_story", "")) > 50:
            print(f"  [{i+1}/{total}] {p['name']} - already has life_story, skipping")
            continue

        print(f"  [{i+1}/{total}] {p['name']} ...", end=" ")
        try:
            life_story = call_llm_for_persona(p, lang)
            if life_story and len(life_story) > 50:
                p["life_story"] = life_story
                print(f"OK ({len(life_story)} chars)")
            else:
                print(f"EMPTY ({len(life_story)} chars)")
        except Exception as e:
            print(f"ERROR: {e}")
            continue

    save_personas(json_path, personas)


def main():
    data_dir = PROJECT_ROOT / "data" / "personas"
    files = {
        "zh_cn.json": "zh",
        "en.json": "en",
        "ja.json": "ja",
        "ko.json": "ko",
    }

    for filename, lang in files.items():
        path = data_dir / filename
        if path.exists():
            enrich_file(path, lang)
        else:
            print(f"Not found: {path}")

    print("\nAll done!")


if __name__ == "__main__":
    main()
