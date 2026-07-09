#!/usr/bin/env python3
"""
为现有人设 JSON 批量补充立体化字段：
hobbies, values, fears, love_view, daily_routine, favorite_things
"""

import json
import os
import sys
from pathlib import Path

# 将项目根目录加入路径
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
    "zh": """你是一个专业的人物设定编剧。请为以下人设补充6个新字段，让人设更立体真实。

要求：
1. hobbies（兴趣爱好）：3-5个具体的爱好，要贴合人物背景和性格，用顿号分隔
2. values（核心价值观）：一句话概括这个人最看重的东西
3. fears（内心脆弱点）：一个具体的内心恐惧或软肋，不是表面的，而是深层的心理
4. love_view（恋爱观）：一句话描述这个人对恋爱的态度和期待
5. daily_routine（典型一天）：2-3句话描述这个人普通一天的生活节奏
6. favorite_things（喜欢的东西）：喜欢的音乐/电影/食物/物品等，3-5个，用顿号分隔

注意：内容必须符合东亚文化语境和中国城市生活现实，语言要口语化、真实感强。

请直接返回一个 JSON 数组，每个元素是一个对象，包含这6个字段。不要返回任何解释文字。

人设列表：
{profiles}
""",
    "en": """You are a professional character writer. Please enrich the following personas with 6 new fields to make them more three-dimensional and realistic.

Requirements:
1. hobbies: 3-5 specific hobbies that fit the background and personality, comma-separated
2. values: One sentence summarizing what this person values most
3. fears: A specific inner vulnerability or deep-seated fear, not surface-level
4. love_view: One sentence describing their attitude and expectations toward relationships
5. daily_routine: 2-3 sentences describing an ordinary day in their life
6. favorite_things: Favorite music/movies/food/items, 3-5 things, comma-separated

Note: Content must fit Western/American cultural context and urban life realities. Language should be colloquial and authentic.

Return ONLY a JSON array. Each element is an object with these 6 fields. No explanatory text.

Personas:
{profiles}
""",
    "ja": """あなたはプロのキャラクター設定作家です。以下のキャラクターに6つの新しいフィールドを追加し、より立体的でリアルな人物像にしてください。

要件：
1. hobbies（趣味）：背景と性格に合った具体的な趣味を3〜5個、頓号で区切る
2. values（核心的価値観）：この人物が最も大切にしていることを一文で概括
3. fears（内面の脆さ）：表面的ではなく、深層心理にある具体的な恐怖や弱み
4. love_view（恋愛観）：恋愛に対する態度と期待を一文で描写
5. daily_routine（典型的な一日）：この人物の普通の一日の生活リズムを2〜3文で描写
6. favorite_things（好きなもの）：好きな音楽・映画・食べ物・物など、3〜5個、頓号で区切る

注意：内容は日本文化の文脈と日本の都市生活の現実に合っている必要がある。言葉は口語的でリアル感があること。

JSON配列のみを返してください。各要素はこの6つのフィールドを含むオブジェクトです。説明文字は一切不要。

キャラクター一覧：
{profiles}
""",
    "ko": """당신은 전문 캐릭터 설정 작가입니다. 다음 인물들에게 6개의 새로운 필드를 추가하여 더 입체적이고 현실적인 인물상으로 만들어주세요.

요구사항:
1. hobbies（취미）：배경과 성격에 맞는 구체적인 취미 3~5개, 쉼표로 구분
2. values（핵심 가치관）：이 인물이 가장 중요하게 여기는 것을 한 문장으로 요약
3. fears（남겨진 상처와 두려움）：표면적이지 않고 깊은 심리에 있는 구체적인 두려움이나 약점
4. love_view（연애관）：연애에 대한 태도와 기대를 한 문장으로 묘사
5. daily_routine（평범한 하루）：이 인물의 평범한 하루 생활 리듬을 2~3문장으로 묘사
6. favorite_things（좋아하는 것들）：좋아하는 음악/영화/음식/물건 등 3~5개, 쉼표로 구분

주의: 내용은 한국 문화 맥락과 한국 도시 생활의 현실에 맞아야 합니다. 언어는 구어체이며 현실감이 있어야 합니다.

JSON 배염만 반환해주세요. 각 요소는 이 6개의 필드를 포함하는 객체입니다. 설명 문자는 전혀 필요 없습니다.

인물 목록：
{profiles}
""",
}


def build_profile_summary(persona: dict, lang: str) -> str:
    """将人设压缩为给 LLM 的摘要"""
    if lang == "zh":
        return (
            f"- {persona['name']}，{persona['age']}岁，{persona['city']}，"
            f"性格：{persona['personality']}。"
            f"背景：{persona['background'][:120]}…"
        )
    elif lang == "en":
        return (
            f"- {persona['name']}, {persona['age']}, {persona['city']}, "
            f"personality: {persona['personality']}. "
            f"Background: {persona['background'][:150]}..."
        )
    elif lang == "ja":
        return (
            f"- {persona['name']}、{persona['age']}歳、{persona['city']}、"
            f"性格：{persona['personality']}。"
            f"背景：{persona['background'][:120]}…"
        )
    else:  # ko
        return (
            f"- {persona['name']}、{persona['age']}살、{persona['city']}、"
            f"성격：{persona['personality']}。"
            f"배경：{persona['background'][:120]}…"
        )


def call_llm_for_batch(summaries: str, lang: str):
    """调用 LLM 为一组人设生成新字段"""
    system_text = "You are a professional character writer. Return only valid JSON."
    user_text = PROMPT_TEMPLATES[lang].format(profiles=summaries)

    llm = get_llm(temperature=0.85, max_tokens=4096)
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

    return json.loads(content)


def enrich_file(json_path: Path, lang: str, batch_size: int = 5):
    print(f"\nProcessing: {json_path.name} ({lang})")
    personas = load_personas(json_path)
    total = len(personas)

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = personas[start:end]
        summaries = "\n".join(build_profile_summary(p, lang) for p in batch)

        print(f"  Batch {start+1}-{end} / {total} ...", end=" ")
        try:
            enriched = call_llm_for_batch(summaries, lang)
            if len(enriched) != len(batch):
                print(f"MISMATCH: expected {len(batch)}, got {len(enriched)}. Skipping batch.")
                continue

            for i, p in enumerate(batch):
                p["hobbies"] = enriched[i].get("hobbies", "")
                p["values"] = enriched[i].get("values", "")
                p["fears"] = enriched[i].get("fears", "")
                p["love_view"] = enriched[i].get("love_view", "")
                p["daily_routine"] = enriched[i].get("daily_routine", "")
                p["favorite_things"] = enriched[i].get("favorite_things", "")

            print("OK")
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
            enrich_file(path, lang, batch_size=5)
        else:
            print(f"Not found: {path}")

    print("\nAll done!")


if __name__ == "__main__":
    main()
