#!/usr/bin/env python3
"""修复缺失的成长经历"""

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.agent import get_llm
from langchain_core.messages import HumanMessage, SystemMessage


def clean_json_text(text: str) -> str:
    """清理可能导致 JSON 解析失败的字符"""
    # 移除控制字符
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f]', '', text)
    # 修复未转义的引号（简单处理）
    return text


def extract_life_story(content: str) -> str:
    """从 LLM 返回内容中提取 life_story"""
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
        return data.get("life_story", "")
    except json.JSONDecodeError:
        # 尝试用正则提取
        match = re.search(r'"life_story"\s*:\s*"(.*?)"\s*}', content, re.DOTALL)
        if match:
            return match.group(1).replace('\\"', '"').replace('\\n', '\n')
        # 如果都失败了，直接返回整个内容
        return content[:2000]


def call_llm(summary: str, lang: str) -> str:
    prompts = {
        "zh": f"""请为以下人设撰写一段完整的成长经历（life_story），300-600字。
要求包含：童年、青少年、成年、原生家庭影响、重大转折点、内心创伤与成长。
语言口语化、有画面感，像深夜倾诉往事。

{summary}

请只返回JSON：{{"life_story": "..."}}""",
        "en": f"""Write a complete life story (300-600 words) for this character.
Include: childhood, adolescence, early adulthood, family influence, turning point, inner wounds and growth.
Colloquial, vivid, like pouring heart out to a friend.

{summary}

Return ONLY JSON: {{"life_story": "..."}}""",
        "ja": f"""以下のキャラクターの成長経歴（300〜600字）を書いてください。
幼少期、思春期、青年期、原生家庭の影響、転換点、内面の傷と成長を含めてください。
口語的で、情景が浮かぶ語り口で。

{summary}

JSONのみ返してください：{{"life_story": "..."}}""",
        "ko": f"""다음 인물의 성장 이야기(300~600자)를 써주세요.
유년기, 청소년기, 청년기, 원생가정 영향, 전환점, 납겨진 상처와 성장을 포함하세요.
구어체이고 장면이 떠오르는 말투로.

{summary}

JSON만 반환해주세요：{{"life_story": "..."}}""",
    }

    llm = get_llm(temperature=0.9, max_tokens=2048)
    messages = [
        SystemMessage(content="Return only valid JSON with field 'life_story'."),
        HumanMessage(content=prompts.get(lang, prompts["zh"])),
    ]
    resp = llm.invoke(messages)
    content = resp.content if hasattr(resp, "content") else str(resp)
    return extract_life_story(content)


def fix_file(json_path: Path, lang: str):
    print(f"\nFixing: {json_path.name}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    missing = [(i, p) for i, p in enumerate(data) if not p.get("life_story") or len(p.get("life_story", "")) < 50]
    print(f"  {len(missing)} missing")

    for idx, p in missing:
        summary = f"""姓名: {p.get('name')}
年龄: {p.get('age')}
性别: {p.get('gender')}
城市: {p.get('city')}
性格: {p.get('personality')}
背景: {p.get('background')}
核心价值观: {p.get('values')}
内心脆弱点: {p.get('fears')}
恋爱观: {p.get('love_view')}"""

        print(f"  [{idx+1}] {p['name']} ...", end=" ")
        try:
            life_story = call_llm(summary, lang)
            if life_story and len(life_story) > 50:
                p["life_story"] = life_story
                print(f"OK ({len(life_story)} chars)")
            else:
                print(f"EMPTY")
        except Exception as e:
            print(f"ERROR: {e}")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Saved.")


def main():
    data_dir = PROJECT_ROOT / "data" / "personas"
    fix_file(data_dir / "zh_cn.json", "zh")
    fix_file(data_dir / "en.json", "en")
    fix_file(data_dir / "ja.json", "ja")
    print("\nDone!")


if __name__ == "__main__":
    main()
