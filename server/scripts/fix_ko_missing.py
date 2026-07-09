#!/usr/bin/env python3
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.agent import get_llm
from langchain_core.messages import HumanMessage

with open(PROJECT_ROOT / "data" / "personas" / "ko.json", "r", encoding="utf-8") as f:
    data = json.load(f)

missing = [i for i, p in enumerate(data) if not p.get("hobbies")]
print("Missing indices:", missing)

for idx in missing:
    p = data[idx]
    summary = f"- {p['name']}、{p['age']}살、{p['city']}、성격：{p['personality']}。배경：{p['background'][:120]}…"
    prompt = f"""당신은 전문 캐릭터 설정 작가입니다. 다음 인물에게 6개의 새로운 필드를 추가해주세요.

요구사항:
1. hobbies（취미）：구체적인 취미 3~5개, 쉼표로 구분
2. values（핵심 가치관）：한 문장으로 요약
3. fears（남겨진 상처와 두려움）：깊은 심리의 두려움
4. love_view（연애관）：한 문장
5. daily_routine（평범한 하루）：2~3문장
6. favorite_things（좋아하는 것들）：3~5개, 쉼표로 구분

{summary}

JSON 객체 하나만 반환하세요. 설명 없이."""
    try:
        llm = get_llm(temperature=0.85, max_tokens=1024)
        resp = llm.invoke([HumanMessage(content=prompt)])
        content = resp.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        result = json.loads(content.strip())
        p["hobbies"] = result.get("hobbies", "")
        p["values"] = result.get("values", "")
        p["fears"] = result.get("fears", "")
        p["love_view"] = result.get("love_view", "")
        p["daily_routine"] = result.get("daily_routine", "")
        p["favorite_things"] = result.get("favorite_things", "")
        print(f"[{idx}] OK")
    except Exception as e:
        print(f"[{idx}] FAIL: {e}")

with open(PROJECT_ROOT / "data" / "personas" / "ko.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("Saved.")
