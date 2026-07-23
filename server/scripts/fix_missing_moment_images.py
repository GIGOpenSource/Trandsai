#!/usr/bin/env python3
"""
为使用旧路径或异常URL的朋友圈重新生成图片并上传到COS。
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import get_db, MomentORM, CompanionORM
from services.image_generation import generate_image_with_cache, generate_moment_image_prompt


def main():
    with get_db() as db:
        rows = db.query(MomentORM).filter(
            (MomentORM.image_url.like("/data/images/%")) |
            (MomentORM.image_url.like("%picsum%")) |
            (MomentORM.image_url.like("%placeholder%")) |
            (MomentORM.image_url.like("%x.ai%")) |
            (MomentORM.image_url.like("%imgen%")) |
            (MomentORM.image_url.is_(None)) |
            (MomentORM.image_url == "")
        ).all()
        # 在 session 关闭前提取所有数据
        moments = [
            {"id": r.id, "caption": r.caption, "image_url": r.image_url or "", "companion_id": r.companion_id}
            for r in rows
            if r.caption  # 只处理有文案的朋友圈
        ]

    if not moments:
        print("没有需要修复的朋友圈配图")
        return

    print(f"找到 {len(moments)} 条需要修复配图的朋友圈")
    success = 0
    failed = 0

    for i, m in enumerate(moments, 1):
        try:
            # 加载伴侣资料以个性化prompt
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
            new_url = generate_image_with_cache(prompt, style=img_style, width=600, height=600)

            if new_url and new_url.startswith("http"):
                with get_db() as db:
                    r = db.query(MomentORM).filter(MomentORM.id == m["id"]).first()
                    if r:
                        r.image_url = new_url
                        db.commit()
                success += 1
                print(f"  [{i}/{len(moments)}] ✓ 朋友圈 #{m['id']} 配图已修复 -> {new_url[:60]}...")
            else:
                failed += 1
                print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 生成失败")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 失败: {e}")

        # 避免请求过快
        time.sleep(0.5)

    print(f"\n完成: 成功 {success} 个, 失败 {failed} 个")


if __name__ == "__main__":
    main()
