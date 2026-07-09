#!/usr/bin/env python3
"""
批量重新生成所有占位图片（pollinations.ai 和本地缓存）。
包括智能体头像和朋友圈配图，均使用配置的图片生成服务（火山引擎）。
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from concurrent.futures import ThreadPoolExecutor, as_completed
from core.database import get_db, CompanionORM, MomentORM
from services.image_generation import (
    generate_avatar_prompt,
    generate_moment_image_prompt,
    generate_image_with_cache,
    IMAGE_CACHE_DIR,
)


def process_avatar(companion_id: str, style: str = "anime") -> dict:
    """处理单个智能体头像"""
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
            prompt = generate_avatar_prompt(profile, style=style)
            # 根据风格选择增强词
            cache_style = "realistic" if style == "realistic" else "portrait"
            url = generate_image_with_cache(prompt, style=cache_style, width=512, height=512)

            if url:
                companion.avatar_url = url
                return {"id": companion_id, "ok": True, "url": url}
            else:
                return {"id": companion_id, "ok": False, "error": "生成失败"}
    except Exception as e:
        return {"id": companion_id, "ok": False, "error": str(e)}


def process_moment(moment_id: int) -> dict:
    """处理单条朋友圈配图，使用伴侣个人资料增强prompt"""
    try:
        with get_db() as db:
            moment = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
            if not moment:
                return {"id": moment_id, "ok": False, "error": "朋友圈不存在"}

            # 加载伴侣个人资料以生成个性化prompt
            from core.database import CompanionORM
            companion = db.query(CompanionORM).filter(
                CompanionORM.id == moment.companion_id
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
                return {"id": moment_id, "ok": True, "url": url}
            else:
                return {"id": moment_id, "ok": False, "error": "生成失败"}
    except Exception as e:
        return {"id": moment_id, "ok": False, "error": str(e)}


def regenerate_avatars(max_workers: int = 3, style: str = "anime", all_companions: bool = False):
    """重新生成头像

    Args:
        max_workers: 并发数
        style: 头像风格，"anime" 或 "realistic"
        all_companions: 为 True 时重新生成所有智能体头像，否则只重生成占位图片
    """
    with get_db() as db:
        if all_companions:
            rows = db.query(CompanionORM).all()
        else:
            # 只针对异常/占位头像（避免重新生成所有正常本地图片）
            rows = db.query(CompanionORM).filter(
                (CompanionORM.avatar_url.like("%pollinations%")) |
                (CompanionORM.avatar_url.like("%picsum%")) |
                (CompanionORM.avatar_url.like("%dicebear%")) |
                (CompanionORM.avatar_url.like("%placeholder%")) |
                (CompanionORM.avatar_url.like("%x.ai%")) |
                (CompanionORM.avatar_url == "__GENERATING__") |
                (CompanionORM.avatar_url.is_(None)) |
                (CompanionORM.avatar_url == "")
            ).all()
        companion_ids = [r.id for r in rows]

    total = len(companion_ids)
    if total == 0:
        print("没有需要重新生成的头像")
        return 0, 0

    print(f"\n=== 开始重新生成头像 ({total} 个, 风格={style}) ===")
    success = 0
    failed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {executor.submit(process_avatar, cid, style): cid for cid in companion_ids}
        for i, future in enumerate(as_completed(future_to_id), 1):
            result = future.result()
            if result["ok"]:
                success += 1
            else:
                failed += 1
                print(f"  [{i}/{total}] 头像失败 ID={result['id']}: {result.get('error', '')}")

            if i % 10 == 0 or i == total:
                elapsed = time.time() - start_time
                avg = elapsed / i if i > 0 else 0
                eta = avg * (total - i)
                print(f"  进度: {i}/{total} | 成功: {success} | 失败: {failed} | ETA: {int(eta)}s")

    print(f"头像生成完成: 成功 {success}, 失败 {failed}")
    return success, failed


def regenerate_moments(max_workers: int = 3):
    """重新生成所有异常朋友圈配图（包括picsum、x.ai临时URL、placeholder、/data/images/等）"""
    with get_db() as db:
        rows = db.query(MomentORM).filter(
            (MomentORM.image_url.like("%x.ai%")) |
            (MomentORM.image_url.like("%imgen%")) |
            (MomentORM.image_url.like("%picsum%")) |
            (MomentORM.image_url.like("%placeholder%")) |
            (MomentORM.image_url.is_(None)) |
            (MomentORM.image_url == "")
        ).all()
        moment_ids = [r.id for r in rows]

    total = len(moment_ids)
    if total == 0:
        print("没有需要重新生成的朋友圈配图")
        return 0, 0

    print(f"\n=== 开始重新生成朋友圈配图 ({total} 条) ===")
    success = 0
    failed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {executor.submit(process_moment, mid): mid for mid in moment_ids}
        for i, future in enumerate(as_completed(future_to_id), 1):
            result = future.result()
            if result["ok"]:
                success += 1
            else:
                failed += 1
                if failed <= 5:
                    print(f"  [{i}/{total}] 朋友圈失败 ID={result['id']}: {result.get('error', '')}")

            if i % 50 == 0 or i == total:
                elapsed = time.time() - start_time
                avg = elapsed / i if i > 0 else 0
                eta = avg * (total - i)
                print(f"  进度: {i}/{total} | 成功: {success} | 失败: {failed} | ETA: {int(eta//60)}m{int(eta%60)}s")

    print(f"朋友圈配图生成完成: 成功 {success}, 失败 {failed}")
    return success, failed


def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量重新生成头像和朋友圈配图")
    parser.add_argument("--style", choices=["anime", "realistic"], default="anime",
                        help="头像风格: anime(动漫, 默认) 或 realistic(写实)")
    parser.add_argument("--all", action="store_true",
                        help="重新生成所有智能体头像（默认只重生成占位图片）")
    parser.add_argument("--avatars-only", action="store_true",
                        help="仅重新生成头像，跳过朋友圈配图")
    parser.add_argument("--workers", type=int, default=3,
                        help="并发 worker 数量（默认 3）")
    args = parser.parse_args()

    print("=" * 60)
    print("批量重新生成异常/占位图片（基于朋友圈文案 + 机器人个人资料）")
    print(f"  头像风格: {args.style}")
    print(f"  全部重生成: {args.all}")
    print("=" * 60)

    total_start = time.time()

    # 1. 头像
    avatar_success, avatar_failed = regenerate_avatars(
        max_workers=args.workers,
        style=args.style,
        all_companions=args.all,
    )

    # 2. 朋友圈配图
    moment_success, moment_failed = 0, 0
    if not args.avatars_only:
        moment_success, moment_failed = regenerate_moments(max_workers=args.workers)

    total_elapsed = time.time() - total_start
    print("\n" + "=" * 60)
    print("全部完成!")
    print(f"  头像: 成功 {avatar_success}, 失败 {avatar_failed}")
    if not args.avatars_only:
        print(f"  朋友圈: 成功 {moment_success}, 失败 {moment_failed}")
    print(f"  总耗时: {int(total_elapsed//60)}m{int(total_elapsed%60)}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
