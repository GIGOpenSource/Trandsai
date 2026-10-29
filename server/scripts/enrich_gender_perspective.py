#!/usr/bin/env python3
"""
为现有人设 JSON 批量补充个性化性别观念与认知 gender_perspective
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


def extract_field(content: str, field: str) -> str:
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
        return data.get(field, "")
    except json.JSONDecodeError:
        match = re.search(rf'"{field}"\s*:\s*"(.*?)"\s*}}', content, re.DOTALL)
        if match:
            return match.group(1).replace('\\"', '"').replace('\\n', '\n')
        return content[:1000]


PROMPTS = {
    "zh": """你是一个专业的人物设定师。请为以下人设撰写一段【性别观念与认知】，约200-400字。

必须深度涵盖以下维度：
1. 性别角色认知：TA 认为男生/女生"应该"是什么样的？这种认知受什么影响？（原生家庭、成长环境、教育背景）
2. 对性别平等的态度：TA 是传统的还是现代的？对"男主外女主内""女生要温柔"这类观念怎么看？
3. 性取向与情感认同：TA 的性取向是什么？TA 如何看待 LGBTQ+？对多元化恋爱关系的态度？
4. 恋爱中的性别期待：TA 在恋爱中希望对方扮演什么角色？TA 自己又想扮演什么角色？
5. 自我性别认同：TA 对自己的性别身份满意吗？有没有过困惑或挣扎？
6. 对社会性别规训的态度：TA 是否感受到来自社会的性别压力？如何应对？

要求：
- 必须基于该人物的具体设定生成，不能模板化
- 口语化、有画面感，像这个人物在深夜跟朋友倾诉
- 语言为中文

人设信息：
{profile}

请只返回 JSON：{{"gender_perspective": "..."}}""",
    "en": """You are a professional character designer. Write a 【Gender Perspective & Cognition】section for this character, about 200-400 words.

Must deeply cover:
1. Gender role perception: What does the character think men/women "should" be like? What influenced this view?
2. Attitude toward gender equality: Traditional or modern? Views on "men work outside, women inside" type norms?
3. Sexual orientation & emotional identity: Their orientation? Views on LGBTQ+? Attitude toward diverse relationships?
4. Gender expectations in romance: What role do they want their partner to play? What role do they want to play?
5. Self-gender identity: Are they comfortable with their gender? Any confusion or struggle?
6. Attitude toward societal gender norms: Do they feel pressure from society about gender? How do they cope?

Requirements:
- Must be based on this character's specific background, NOT templated
- Colloquial, vivid, like pouring heart out to a friend late at night
- Language must be English

Character info:
{profile}

Return ONLY JSON: {{"gender_perspective": "..."}}""",
    "ja": """あなたはプロのキャラクターデザイナーです。以下のキャラクターに【ジェンダー観・性別認識】（200〜400字）を書いてください。

必ず以下の次元を深く掘り下げて：
1. ジェンダー役割の認識：そのキャラクターは男性・女性「らしさ」をどう考える？何の影響を受けた？
2. ジェンダー平等への態度：伝統的か現代的か？「男は外で働き、女は家を守る」といった観念にどう向き合う？
3. 性的指向と感情のアイデンティティ：その人の性的指向は？LGBTQ+ をどう見る？多様な恋愛関係に対する態度は？
4. 恋愛におけるジェンダー期待：相手にどんな役割を期待する？自分はどんな役割を演じたい？
5. 自己ジェンダーアイデンティティ：自分の性別に満足している？葛藤や迷いはあった？
6. 社会のジェンダー規範への態度：社会からのジェンダー圧力を感じる？どう対処する？

要件：
- キャラクターの具体的な設定に基づくこと——テンプレート禁止
- 口語的で、深夜に友人に心を開くような語り口
- 言語は日本語

キャラクター情報：
{profile}

JSONのみ返してください：{{"gender_perspective": "..."}}""",
    "ko": """당신은 프로 캐릭터 디자이너입니다. 다음 인물에게【젠더관·성별 인식】（200~400자）을 써주세요.

반드시 다음 차원을 깊이 있게 다루세요：
1. 젠더 역할 인식：이 인물은 남성·여성이 '어때야 한다'고 생각하는가？무엇의 영향을 받았는가？
2. 젠더 평등에 대한 태도：전통적인가 현대적인가？'남자는 밖에서 일하고 여자는 집을 지킨다'는 관념에 어떻게 대하는가？
3. 성적 지향과 감정 정체성：이 인물의 성적 지향은？LGBTQ+를 어떻게 보는가？다양한 연애 관계에 대한 태도는？
4. 연애에서의 젠더 기대：상대에게 어떤 역할을 기대하는가？자신은 어떤 역할을 하고 싶은가？
5. 자기 젠더 정체성：자신의 성별에 만족하는가？갈등이나 혼란이 있었는가？
6. 사회의 젠더 규범에 대한 태도：사회로부터의 젠더 압력을 느끼는가？어떻게 대처하는가？

요구사항：
- 인물의 구체적인 설정에 기반해야 함——템플릿 금지
- 구어체이고 깊은 밤에 친구에게 속삭이는 말투
- 언어는 한국어

인물 정보：
{profile}

JSON만 반환해주세요：{{"gender_perspective": "..."}}""",
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
        f"恋爱观: {persona.get('love_view', '')}",
        f"文化三观: {persona.get('cultural_values', '')[:200]}...",
        f"成长经历: {persona.get('life_story', '')[:200]}...",
    ]
    return "\n".join(fields)


def call_llm_for_persona(persona: dict, lang: str) -> str:
    summary = build_profile_summary(persona, lang)
    system_text = "Return only valid JSON with a single field 'gender_perspective'."
    user_text = PROMPTS[lang].format(profile=summary)

    llm = get_llm(temperature=0.9, max_tokens=2048)
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=user_text),
    ]
    resp = llm.invoke(messages)
    content = resp.content if hasattr(resp, "content") else str(resp)
    return extract_field(content, "gender_perspective")


def enrich_file(json_path: Path, lang: str):
    print(f"\nProcessing: {json_path.name} ({lang})")
    personas = load_personas(json_path)
    total = len(personas)

    for i, p in enumerate(personas):
        if p.get("gender_perspective") and len(p.get("gender_perspective", "")) > 50:
            print(f"  [{i+1}/{total}] {p['name']} - already has, skipping")
            continue

        print(f"  [{i+1}/{total}] {p['name']} ...", end=" ")
        try:
            gp = call_llm_for_persona(p, lang)
            if gp and len(gp) > 50:
                p["gender_perspective"] = gp
                print(f"OK ({len(gp)} chars)")
            else:
                print(f"EMPTY ({len(gp)} chars)")
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
