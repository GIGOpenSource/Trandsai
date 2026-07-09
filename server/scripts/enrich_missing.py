#!/usr/bin/env python3
"""补充缺失的立体化字段"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.agent import get_llm
from langchain_core.messages import HumanMessage, SystemMessage

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
    if lang == "zh":
        return (
            f"- {persona['name']}，{persona['age']}岁，{persona['city']}，"
            f"性格：{persona['personality']}。"
            f"背景：{persona['background'][:120]}…"
        )
    else:
        return (
            f"- {persona['name']}、{persona['age']}살、{persona['city']}、"
            f"성격：{persona['personality']}。"
            f"배경：{persona['background'][:120]}…"
        )


def call_llm(summaries: str, lang: str):
    system_text = "You are a professional character writer. Return only valid JSON."
    user_text = PROMPT_TEMPLATES[lang].format(profiles=summaries)
    llm = get_llm(temperature=0.85, max_tokens=4096)
    messages = [SystemMessage(content=system_text), HumanMessage(content=user_text)]
    resp = llm.invoke(messages)
    content = resp.content if hasattr(resp, "content") else str(resp)
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return json.loads(content.strip())


def enrich_missing(json_path: Path, lang: str, missing_indices: list, batch_size: int = 3):
    print(f"\nFixing {json_path.name}: {len(missing_indices)} missing")
    with open(json_path, "r", encoding="utf-8") as f:
        personas = json.load(f)

    for start in range(0, len(missing_indices), batch_size):
        batch_idx = missing_indices[start:start + batch_size]
        batch = [personas[i] for i in batch_idx]
        summaries = "\n".join(build_profile_summary(p, lang) for p in batch)

        print(f"  Batch indices {batch_idx} ...", end=" ")
        try:
            enriched = call_llm(summaries, lang)
            if len(enriched) != len(batch):
                print(f"MISMATCH {len(enriched)} vs {len(batch)}. Retry with smaller batch.")
                # 逐个处理
                for i, idx in enumerate(batch_idx):
                    p = personas[idx]
                    summary = build_profile_summary(p, lang)
                    try:
                        single = call_llm(summary, lang)
                        data = single[0] if isinstance(single, list) else single
                        p["hobbies"] = data.get("hobbies", "")
                        p["values"] = data.get("values", "")
                        p["fears"] = data.get("fears", "")
                        p["love_view"] = data.get("love_view", "")
                        p["daily_routine"] = data.get("daily_routine", "")
                        p["favorite_things"] = data.get("favorite_things", "")
                        print(f"[{idx}]OK", end=" ")
                    except Exception as e2:
                        print(f"[{idx}]FAIL:{e2}", end=" ")
                print()
                continue

            for i, idx in enumerate(batch_idx):
                p = personas[idx]
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

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(personas, f, ensure_ascii=False, indent=2)
    print(f"  Saved.")


def main():
    data_dir = PROJECT_ROOT / "data" / "personas"

    # zh_cn: indices 25-29 missing
    enrich_missing(data_dir / "zh_cn.json", "zh", [25, 26, 27, 28, 29], batch_size=3)

    # ko: indices 0-4, 25-34 missing
    enrich_missing(data_dir / "ko.json", "ko", [0, 1, 2, 3, 4, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34], batch_size=3)

    print("\nDone!")


if __name__ == "__main__":
    main()
