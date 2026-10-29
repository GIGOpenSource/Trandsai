#!/usr/bin/env python3
"""
批量为没有头像的智能体生成头像。
使用 Pollinations.ai 生成头像 URL 并更新到数据库。
"""

import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import get_db, CompanionORM
from services.image_generation import generate_avatar_prompt, generate_image


def main():
    # 先提取所有需要的数据到普通列表（避免 session 关闭后访问 ORM 对象）
    with get_db() as db:
        rows = db.query(CompanionORM).filter(
            (CompanionORM.avatar_url == None) | (CompanionORM.avatar_url == "")
        ).all()
        companions = [
            {
                "id": row.id,
                "name": row.name,
                "age": row.age or 22,
                "gender": row.gender or "女",
                "city": row.city or "",
                "personality": row.personality or "",
                "background": row.background or "",
                "speech_style": row.speech_style or "",
                "hobbies": row.hobbies or "",
                "values": row.values or "",
                "favorite_things": row.favorite_things or "",
                "mbti": row.mbti or "",
            }
            for row in rows
        ]

    if not companions:
        print("所有智能体已有头像，无需生成")
        return

    print(f"找到 {len(companions)} 个没有头像的智能体")
    success = 0
    failed = 0

    for c in companions:
        prompt = generate_avatar_prompt(c)

        try:
            avatar_url = generate_image(prompt, style="portrait", width=512, height=512)
            if avatar_url:
                with get_db() as db:
                    r = db.query(CompanionORM).filter(CompanionORM.id == c["id"]).first()
                    if r:
                        r.avatar_url = avatar_url
                success += 1
                print(f"  ✓ [{c['id']}] {c['name']} -> 头像已生成")
            else:
                failed += 1
                print(f"  ✗ [{c['id']}] {c['name']} -> 生成失败（空URL）")
        except Exception as e:
            failed += 1
            print(f"  ✗ [{c['id']}] {c['name']} -> 生成失败: {e}")

        # 避免请求过快，间隔 0.5 秒
        time.sleep(0.5)

    print(f"\n完成: 成功 {success} 个, 失败 {failed} 个")


if __name__ == "__main__":
    main()
