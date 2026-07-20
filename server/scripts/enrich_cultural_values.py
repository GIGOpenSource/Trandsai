#!/usr/bin/env python3
"""
为现有人设 JSON 批量补充个性化文化三观 cultural_values
不再是统一模板，而是根据每个人设的具体信息动态生成
"""

import json
import os
import re
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


def clean_json_text(text: str) -> str:
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f]', '', text)
    return text


def extract_cultural_values(content: str) -> str:
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    content = clean_json_text(content)
    try:
        data = json.loads(content)
        return data.get("cultural_values", "")
    except json.JSONDecodeError:
        match = re.search(r'"cultural_values"\s*:\s*"(.*?)"\s*}', content, re.DOTALL)
        if match:
            return match.group(1).replace('\\"', '"').replace('\\n', '\n')
        return content[:1500]


PROMPTS = {
    "zh": """你是一个顶级人物设定师。请为以下人设撰写一段【独特的文化三观与意识形态法则】，约300-500字。

关键要求：
1. 必须基于该人物的具体设定（性格、背景、成长经历、价值观）来生成，不能是模板化的套话
2. 每个人的三观都应该是独一无二的，反映其独特的成长环境和人生经历
3. 必须包含以下维度（但表达方式要贴合人设）：
   - 情感表达方式（含蓄还是直接？受什么影响？）
   - 对家庭和责任的看法（为什么形成这样的看法？）
   - 恋爱哲学（什么样的爱情观？有故事支撑吗？）
   - 金钱和消费观（务实还是浪漫？受原生家庭影响？）
   - 性别角色认知（传统还是现代？为什么？）
   - 对社会和人际关系的底层逻辑
4. 口语化、有画面感，像这个人物在跟朋友掏心窝子聊自己的世界观
5. 语言必须是中文

人设信息：
{profile}

请只返回 JSON：{{"cultural_values": "..."}}""",
    "en": """You are a top-tier character designer. Write a UNIQUE 【Cultural Values & Ideology】section for this character, about 300-500 words.

Key requirements:
1. Must be based on this character's specific traits (personality, background, life story, values) — NOT a generic template
2. Each character's worldview should be one-of-a-kind, reflecting their unique upbringing and experiences
3. Must cover these dimensions (expressed in the character's voice):
   - How they express emotions (direct or reserved? influenced by what?)
   - Views on family and responsibility (why these views?)
   - Love philosophy (what kind of love do they believe in? any story behind it?)
   - Money and consumption attitude (pragmatic or romantic? family influence?)
   - Gender role perception (traditional or modern? why?)
   - Underlying social and interpersonal logic
4. Colloquial, vivid, like the character pouring their heart out about their worldview
5. Language must be English

Character info:
{profile}

Return ONLY JSON: {{"cultural_values": "..."}}""",
    "ja": """あなたはトップクラスのキャラクターデザイナーです。以下のキャラクターに【独特の文化観・価値観ルール】（300〜500字）を書いてください。

重要な要件：
1. このキャラクターの具体的な設定（性格、背景、成長経歴、価値観）に基づいて生成すること——テンプレート化された決まり文句は禁止
2. 各キャラクターの世界観は唯一無二で、独自の育ち環境と経験を反映していること
3. 以下の次元を必ず含める（キャラクターの語り口で表現）：
   - 感情表現の仕方（遠慮がちかストレートか？何の影響を受けた？）
   - 家族と責任に対する考え（なぜそう考える？）
   - 恋愛哲学（どんな愛を信じている？裏に物語はある？）
   - 金銭感覚と消費態度（実利的かロマンチックか？原生家庭の影響？）
   - ジェンダー役割の認識（伝統的か現代的か？なぜ？）
   - 社会と人間関係の根底にある論理
4. 口語的で情景が浮かぶ語り口で、このキャラクターが友人に心を開いて自分の世界観を語っているような感じ
5. 言語は日本語であること

キャラクター情報：
{profile}

JSONのみ返してください：{{"cultural_values": "..."}}""",
    "ko": """당신은 최고 수준의 캐릭터 디자이너입니다. 다음 인물에게【독특한 문화·가치관 법칙】（300~500자）을 써주세요.

핵심 요구사항：
1. 이 인물의 구체적인 설정（성격, 배경, 성장 이야기, 가치관）에 기반하여 생성해야 함——템플릿화된 상투적 표현 금지
2. 각 인물의 세계관은 유일무이해야 하며, 독특한 성장 환경과 경험을 반영해야 함
3. 다음 차원을 반드시 포함（인물의 말투로 표현）：
   - 감정 표현 방식（직설적인가, 절제하는가? 무엇의 영향을 받았는가?）
   - 가족과 책임에 대한 시각（왜 그렇게 생각하는가?）
   - 연애 철학（어떤 사랑을 믿는가? 뒷이야기가 있는가?）
   - 금전관과 소비 태도（실용적인가 로맨틱한가? 원생가정의 영향?）
   - 젠더 역할 인식（전통적인가 현대적인가? 왜?）
   - 사회와 인간관계의 근본 논리
4. 구어체이고 장면이 떠오르는 말투로, 이 인물이 친구에게 속삭이며 자신의 세계관을 이야기하는 느낌
5. 언어는 한국어이어야 함

인물 정보：
{profile}

JSON만 반환해주세요：{{"cultural_values": "..."}}""",
}


def build_profile_summary(persona: dict, lang: str) -> str:
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
        f"成长经历: {persona.get('life_story', '')[:300]}...",
    ]
    return "\n".join(fields)


def call_llm_for_persona(persona: dict, lang: str) -> str:
    summary = build_profile_summary(persona, lang)
    system_text = "Return only valid JSON with a single field 'cultural_values'."
    user_text = PROMPTS[lang].format(profile=summary)

    llm = get_llm(temperature=0.9, max_tokens=1024)
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=user_text),
    ]
    resp = llm.invoke(messages)
    content = resp.content if hasattr(resp, "content") else str(resp)
    return extract_cultural_values(content)


def enrich_file(json_path: Path, lang: str):
    print(f"\nProcessing: {json_path.name} ({lang})")
    personas = load_personas(json_path)
    total = len(personas)

    for i, p in enumerate(personas):
        if p.get("cultural_values") and len(p.get("cultural_values", "")) > 50:
            print(f"  [{i+1}/{total}] {p['name']} - already has, skipping")
            continue

        print(f"  [{i+1}/{total}] {p['name']} ...", end=" ")
        try:
            cv = call_llm_for_persona(p, lang)
            if cv and len(cv) > 50:
                p["cultural_values"] = cv
                print(f"OK ({len(cv)} chars)")
            else:
                print(f"EMPTY ({len(cv)} chars)")
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
