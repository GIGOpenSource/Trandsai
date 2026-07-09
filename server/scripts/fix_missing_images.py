#!/usr/bin/env python3
"""
修复缺失的图片文件（数据库有本地缓存URL但文件不存在）。
只重新生成文件缺失的记录，不修改已有文件。
"""

import sys
import os
import time

# 确保工作目录正确
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.getcwd())

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.database import get_db, CompanionORM, MomentORM
from core.config import BASE_DIR
from services.image_generation import (
    generate_avatar_prompt,
    generate_moment_image_prompt,
    generate_image_with_cache,
)

IMAGE_CACHE_DIR = Path(BASE_DIR) / "data" / "images"


def process_avatar(companion_id: str) -> dict:
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

            if url:
                companion.avatar_url = url
                # 验证文件存在
                fname = url.replace("/data/images/", "")
                exists = (IMAGE_CACHE_DIR / fname).exists()
                return {"id": companion_id, "ok": True, "url": url, "file_exists": exists}
            else:
                return {"id": companion_id, "ok": False, "error": "生成失败"}
    except Exception as e:
        return {"id": companion_id, "ok": False, "error": str(e)}


def process_moment(moment_id: int) -> dict:
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

            if url:
                moment.image_url = url
                fname = url.replace("/data/images/", "") if url.startswith("/data/images/") else url
                exists = (IMAGE_CACHE_DIR / fname).exists() if fname.startswith("data") or "/" not in fname else True
                return {"id": moment_id, "ok": True, "url": url, "file_exists": exists}
            else:
                return {"id": moment_id, "ok": False, "error": "生成失败"}
    except Exception as e:
        return {"id": moment_id, "ok": False, "error": str(e)}


def main():
    print("=" * 60)
    print("修复缺失的图片文件")
    print("=" * 60)
    print(f"IMAGE_CACHE_DIR: {IMAGE_CACHE_DIR}")
    print(f"IMAGE_CACHE_DIR exists: {IMAGE_CACHE_DIR.exists()}")

    # 1. 查找缺失头像文件
    with get_db() as db:
        avatar_rows = db.query(CompanionORM).filter(
            CompanionORM.avatar_url.like("/data/images/%")
        ).all()
        missing_avatars = []
        for r in avatar_rows:
            fname = r.avatar_url.replace("/data/images/", "")
            exists = (IMAGE_CACHE_DIR / fname).exists()
            if not exists:
                missing_avatars.append(r.id)
                if len(missing_avatars) <= 3:
                    print(f"  Missing avatar: {r.name} -> {fname}")

    print(f"\n缺失头像文件: {len(missing_avatars)} 个")

    # 2. 查找缺失或异常朋友圈配图文件（包括x.ai临时URL、picsum、placeholder、无效本地文件）
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
        missing_moments = []
        for r in moment_rows:
            url = r.image_url or ""
            if url.startswith("/data/images/"):
                fname = url.replace("/data/images/", "")
                if not (IMAGE_CACHE_DIR / fname).exists() or (IMAGE_CACHE_DIR / fname).stat().st_size < 1000:
                    missing_moments.append(r.id)
            else:
                # 非本地URL（如x.ai临时链接）视为需要重新生成
                missing_moments.append(r.id)

    print(f"缺失朋友圈配图文件: {len(missing_moments)} 条")

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
                if result["ok"] and result.get("file_exists"):
                    success += 1
                elif result["ok"] and not result.get("file_exists"):
                    failed += 1
                    print(f"  [{i}] 头像文件未保存 ID={result['id']}: {result['url']}")
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
                if result["ok"] and result.get("file_exists"):
                    success += 1
                elif result["ok"] and not result.get("file_exists"):
                    failed += 1
                    if failed <= 3:
                        print(f"  [{i}] 朋友圈文件未保存 ID={result['id']}: {result['url']}")
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
