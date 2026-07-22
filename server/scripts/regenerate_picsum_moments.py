#!/usr/bin/env python3
"""
将使用 picsum 占位图的朋友圈配图重新生成为 AI 图片并上传到 COS。
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import get_db, MomentORM
from services.image_generation import (
    generate_image_with_cache,
    generate_moment_image_prompt,
)


def main():
    with get_db() as db:
        rows = db.query(MomentORM).filter(
            MomentORM.image_url.like("%picsum%")
        ).all()
        moments = [{"id": r.id, "caption": r.caption, "companion_id": r.companion_id} for r in rows]

    if not moments:
        print("没有使用 picsum 占位图的朋友圈")
        return

    print(f"找到 {len(moments)} 条使用 picsum 占位图的朋友圈")
    success = 0
    failed = 0

    for i, m in enumerate(moments, 1):
        try:
            # 加载伴侣资料以个性化prompt
            from core.database import CompanionORM
            profile_dict = None
            with get_db() as db:
                companion = db.query(CompanionORM).filter_by(id=m["companion_id"]).first()
                if companion:
                    profile_dict = {
                        "gender": companion.gender or "女",
                        "age": companion.age or 22,
                        "city": companion.city or "",
                        "personality": companion.personality or "",
                        "hobbies": companion.hobbies or "",
                        "mbti": companion.mbti or "",
                    }

            prompt, img_style = generate_moment_image_prompt(m["caption"], profile=profile_dict)
            image_url = generate_image_with_cache(prompt, style=img_style, width=600, height=600)

            if image_url and image_url.startswith("http"):
                with get_db() as db:
                    r = db.query(MomentORM).filter(MomentORM.id == m["id"]).first()
                    if r:
                        r.image_url = image_url
                        db.commit()
                success += 1
                print(f"  [{i}/{len(moments)}] ✓ 朋友圈 #{m['id']} 配图已更新 -> {image_url[:80]}...")
            else:
                failed += 1
                print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 生成失败")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 失败: {e}")

        time.sleep(1)

    print(f"\n完成: 成功 {success} 个, 失败 {failed} 个")


if __name__ == "__main__":
    main()
