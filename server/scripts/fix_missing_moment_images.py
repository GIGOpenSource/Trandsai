#!/usr/bin/env python3
"""
为缺失本地配图文件的朋友圈重新生成图片。
策略：使用 Pollinations.ai 外部 URL（即时可用，无需下载）。
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import get_db, MomentORM
from services.image_generation import generate_image, generate_moment_image_prompt


def main():
    with get_db() as db:
        rows = db.query(MomentORM).filter(
            MomentORM.image_url.isnot(None),
            MomentORM.image_url != "",
            MomentORM.caption.isnot(None),
            MomentORM.caption != ""
        ).all()
        # 在 session 关闭前提取所有数据
        moments = [
            {"id": r.id, "caption": r.caption, "image_url": r.image_url or ""}
            for r in rows
            if (r.image_url or "").startswith("/data/images/")
        ]

    if not moments:
        print("没有需要修复的朋友圈配图")
        return

    print(f"找到 {len(moments)} 条本地配图路径的朋友圈（本地文件已丢失）")
    success = 0
    failed = 0

    for i, m in enumerate(moments, 1):
        prompt, img_style = generate_moment_image_prompt(m["caption"])
        try:
            new_url = generate_image(prompt, style=img_style, width=600, height=600)
            if new_url:
                with get_db() as db:
                    r = db.query(MomentORM).filter(MomentORM.id == m["id"]).first()
                    if r:
                        r.image_url = new_url
                success += 1
                print(f"  [{i}/{len(moments)}] ✓ 朋友圈 #{m['id']} 配图已修复")
            else:
                failed += 1
                print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 生成失败（空URL）")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 失败: {e}")

        # 避免请求过快
        time.sleep(0.3)

    print(f"\n完成: 成功 {success} 个, 失败 {failed} 个")


if __name__ == "__main__":
    main()
