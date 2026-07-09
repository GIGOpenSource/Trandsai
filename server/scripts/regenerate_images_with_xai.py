#!/usr/bin/env python3
"""
使用 xAI (Grok) 模型重新生成图片。
支持：朋友圈配图、伴侣头像。

用法:
    python scripts/regenerate_images_with_xai.py --type moments    # 重新生成所有朋友圈配图
    python scripts/regenerate_images_with_xai.py --type avatars    # 重新生成所有伴侣头像
    python scripts/regenerate_images_with_xai.py --type moments --only-picsum  # 只重新生成 picsum 图片
    python scripts/regenerate_images_with_xai.py --type moments --limit 10     # 只处理前10条

环境变量:
    XAI_API_KEY - xAI API 密钥（必填）
"""

import argparse
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import get_db, MomentORM, CompanionORM
from services.image_generation import (
    generate_moment_image_prompt,
    generate_avatar_prompt,
    generate_image_with_xai_cached,
)


def regenerate_moment_images(only_picsum=False, limit=None):
    """重新生成朋友圈配图"""
    with get_db() as db:
        query = db.query(MomentORM).filter(MomentORM.caption.isnot(None), MomentORM.caption != "")
        if only_picsum:
            query = query.filter(MomentORM.image_url.like("%picsum%"))
        if limit:
            query = query.limit(limit)
        rows = query.all()
        moments = [
            {"id": r.id, "caption": r.caption, "image_url": r.image_url or ""}
            for r in rows
        ]

    if not moments:
        print("没有需要重新生成配图的朋友圈")
        return

    print(f"找到 {len(moments)} 条朋友圈需要重新生成配图")
    success = 0
    failed = 0

    for i, m in enumerate(moments, 1):
        print(f"\n[{i}/{len(moments)}] 处理朋友圈 ID={m['id']}: {m['caption'][:30]}...")
        try:
            prompt, img_style = generate_moment_image_prompt(m["caption"])
            new_url = generate_image_with_xai_cached(prompt, style=img_style, width=1024, height=1024)
            if new_url:
                with get_db() as db:
                    row = db.query(MomentORM).filter_by(id=m["id"]).first()
                    if row:
                        row.image_url = new_url
                print(f"  -> 成功: {new_url}")
                success += 1
            else:
                print(f"  -> 失败: 未生成图片")
                failed += 1
        except Exception as e:
            print(f"  -> 失败: {e}")
            failed += 1
        time.sleep(1)  # 避免请求过快

    print(f"\n完成: 成功 {success}, 失败 {failed}")


def regenerate_avatar_images(limit=None, style: str = "anime"):
    """重新生成伴侣头像"""
    with get_db() as db:
        query = db.query(CompanionORM)
        if limit:
            query = query.limit(limit)
        rows = query.all()
        companions = [
            {
                "id": r.id,
                "name": r.name,
                "age": r.age or 22,
                "gender": r.gender or "女",
                "city": r.city or "",
                "personality": r.personality or "",
                "background": r.background or "",
                "speech_style": r.speech_style or "",
                "hobbies": r.hobbies or "",
                "values": r.values or "",
                "favorite_things": r.favorite_things or "",
                "mbti": r.mbti or "",
            }
            for r in rows
        ]

    if not companions:
        print("没有需要重新生成头像的伴侣")
        return

    print(f"找到 {len(companions)} 个伴侣需要重新生成头像 (风格={style})")
    success = 0
    failed = 0

    for i, c in enumerate(companions, 1):
        print(f"\n[{i}/{len(companions)}] 处理伴侣: {c['name']}")
        try:
            prompt = generate_avatar_prompt(c, style=style)
            cache_style = "realistic" if style == "realistic" else "portrait"
            new_url = generate_image_with_xai_cached(prompt, style=cache_style, width=1024, height=1024)
            if new_url:
                with get_db() as db:
                    row = db.query(CompanionORM).filter_by(id=c["id"]).first()
                    if row:
                        row.avatar_url = new_url
                print(f"  -> 成功: {new_url}")
                success += 1
            else:
                print(f"  -> 失败: 未生成图片")
                failed += 1
        except Exception as e:
            print(f"  -> 失败: {e}")
            failed += 1
        time.sleep(1)

    print(f"\n完成: 成功 {success}, 失败 {failed}")


def main():
    parser = argparse.ArgumentParser(description="使用 xAI 重新生成图片")
    parser.add_argument("--type", choices=["moments", "avatars", "all"], required=True,
                        help="要重新生成的图片类型")
    parser.add_argument("--only-picsum", action="store_true",
                        help="仅重新生成 picsum 随机图片（仅对 moments 有效）")
    parser.add_argument("--limit", type=int, default=None,
                        help="限制处理数量")
    parser.add_argument("--style", choices=["anime", "realistic"], default="anime",
                        help="头像风格: anime(动漫, 默认) 或 realistic(写实)")
    args = parser.parse_args()

    if not os.environ.get("XAI_API_KEY"):
        print("错误: 环境变量 XAI_API_KEY 未设置")
        print("请在 .env 文件中添加 XAI_API_KEY=your_api_key")
        sys.exit(1)

    if args.type == "moments" or args.type == "all":
        regenerate_moment_images(only_picsum=args.only_picsum, limit=args.limit)

    if args.type == "avatars" or args.type == "all":
        if args.only_picsum:
            print("--only-picsum 对 avatars 无效，忽略")
        regenerate_avatar_images(limit=args.limit, style=args.style)


if __name__ == "__main__":
    main()
