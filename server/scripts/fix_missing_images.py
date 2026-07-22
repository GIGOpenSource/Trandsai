#!/usr/bin/env python3
"""
修复数据库中使用旧 /data/images/ 路径的图片记录。
将这些记录重新生成为 AI 图片并上传到 COS。
"""

import sys
import os
import time

# 确保工作目录正确
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.getcwd())

from concurrent.futures import ThreadPoolExecutor, as_completed
from core.database import get_db, CompanionORM, MomentORM
from services.image_generation import (
    generate_avatar_prompt,
    generate_moment_image_prompt,
    generate_image_with_cache,
)


def process_avatar(companion_id: str) -> dict:
    """重新生成头像并上传到COS"""
    try:
        with get_db() as db:
            companion = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
            if not companion:
                return {"id": companion_id, "ok": False, "error": "智能体不存在"}

            profile = {
                "id": companion.id,
                "name": companion.name,
                "age": companion.age or 22,
                "gender": companion.gender or "女",
                "city": companion.city or "",
                "personality": companion.personality or "",
                "background": companion.background or "",
                "speech_style": companion.speech_style or "",
                "hobbies": companion.hobbies or "",
                "values": companion.values or "",
                "favorite_things": companion.favorite_things or "",
                "mbti": companion.mbti or "",
            }
            prompt = generate_avatar_prompt(profile)
            url = generate_image_with_cache(prompt, style="portrait", width=512, height=512)

            if url and url.startswith("http"):
                companion.avatar_url = url
                db.commit()
                return {"id": companion_id, "ok": True, "url": url}
            else:
                return {"id": companion_id, "ok": False, "error": "生成失败或COS上传失败"}
    except Exception as e:
        return {"id": companion_id, "ok": False, "error": str(e)}


def process_moment(moment_id: int) -> dict:
    """重新生成朋友圈配图并上传到COS"""
    try:
        with get_db() as db:
            moment = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
            if not moment:
                return {"id": moment_id, "ok": False, "error": "朋友圈不存在"}

            # 加载伴侣个人资料增强prompt
            from core.database import CompanionORM
            companion = db.query(CompanionORM).filter(
                CompanionORM.id == getattr(moment, 'companion_id', None)
            ).first()

            profile = None
            if companion:
                profile = {
                    "gender": companion.gender or "女",
                    "age": companion.age or 22,
                    "city": companion.city or "",
                    "personality": companion.personality or "",
                    "hobbies": companion.hobbies or "",
                    "mbti": companion.mbti or "",
                }

            prompt, img_style = generate_moment_image_prompt(moment.caption or "", profile=profile)
            url = generate_image_with_cache(prompt, style=img_style, width=600, height=600)

            if url and url.startswith("http"):
                moment.image_url = url
                db.commit()
                return {"id": moment_id, "ok": True, "url": url}
            else:
                return {"id": moment_id, "ok": False, "error": "生成失败或COS上传失败"}
    except Exception as e:
        return {"id": moment_id, "ok": False, "error": str(e)}


def main():
    print("=" * 60)
    print("修复旧 /data/images/ 路径的图片记录")
    print("=" * 60)

    # 1. 查找使用旧路径的头像
    with get_db() as db:
        avatar_rows = db.query(CompanionORM).filter(
            (CompanionORM.avatar_url.like("/data/images/%")) |
            (CompanionORM.avatar_url.like("%picsum%")) |
            (CompanionORM.avatar_url.like("%dicebear%")) |
            (CompanionORM.avatar_url.like("%placeholder%")) |
            (CompanionORM.avatar_url == "__GENERATING__") |
            (CompanionORM.avatar_url.is_(None)) |
            (CompanionORM.avatar_url == "")
        ).all()
        missing_avatars = [r.id for r in avatar_rows]

    print(f"\n需要修复的头像: {len(missing_avatars)} 个")

    # 2. 查找使用旧路径或异常的朋友圈配图
    with get_db() as db:
        moment_rows = db.query(MomentORM).filter(
            (MomentORM.image_url.like("/data/images/%")) |
            (MomentORM.image_url.like("%x.ai%")) |
            (MomentORM.image_url.like("%imgen%")) |
            (MomentORM.image_url.like("%picsum%")) |
            (MomentORM.image_url.like("%placeholder%")) |
            (MomentORM.image_url.is_(None)) |
            (MomentORM.image_url == "")
        ).all()
        missing_moments = [r.id for r in moment_rows]

    print(f"需要修复的朋友圈配图: {len(missing_moments)} 条")

    total_start = time.time()

    # 修复头像
    if missing_avatars:
        print(f"\n=== 修复头像 ({len(missing_avatars)} 个) ===")
        success = 0
        failed = 0
        start = time.time()
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(process_avatar, cid): cid for cid in missing_avatars}
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                if result["ok"]:
                    success += 1
                else:
                    failed += 1
                    print(f"  [{i}] 头像失败 ID={result['id']}: {result.get('error', '')}")

                if i % 10 == 0 or i == len(missing_avatars):
                    elapsed = time.time() - start
                    avg = elapsed / i
                    eta = avg * (len(missing_avatars) - i)
                    print(f"  进度: {i}/{len(missing_avatars)} | 成功: {success} | 失败: {failed} | ETA: {int(eta)}s")
        print(f"头像修复完成: 成功 {success}, 失败 {failed}")

    # 修复朋友圈
    if missing_moments:
        print(f"\n=== 修复朋友圈配图 ({len(missing_moments)} 条) ===")
        success = 0
        failed = 0
        start = time.time()
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(process_moment, mid): mid for mid in missing_moments}
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                if result["ok"]:
                    success += 1
                else:
                    failed += 1
                    if failed <= 3:
                        print(f"  [{i}] 朋友圈失败 ID={result['id']}: {result.get('error', '')}")

                if i % 20 == 0 or i == len(missing_moments):
                    elapsed = time.time() - start
                    avg = elapsed / i
                    eta = avg * (len(missing_moments) - i)
                    print(f"  进度: {i}/{len(missing_moments)} | 成功: {success} | 失败: {failed} | ETA: {int(eta//60)}m{int(eta%60)}s")
        print(f"朋友圈配图修复完成: 成功 {success}, 失败 {failed}")

    total_elapsed = time.time() - total_start
    print("\n" + "=" * 60)
    print("修复完成!")
    print(f"  总耗时: {int(total_elapsed//60)}m{int(total_elapsed%60)}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
