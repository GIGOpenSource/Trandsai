#!/usr/bin/env python3
"""
将使用 picsum 占位图的朋友圈配图重新生成为火山引擎图片。
"""

import sys
import os
import time
import hashlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from core.database import get_db, MomentORM
from services.image_generation import (
    augment_prompt_with_style,
    generate_image_with_volcano,
    generate_moment_image_prompt,
    IMAGE_CACHE_DIR,
    _to_local_image_url,
)


def _download_image(url: str, path: str, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            with open(path, "wb") as f:
                f.write(resp.content)
            return True
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                print(f"    下载失败 ({retries} 次重试): {e}")
    return False


def main():
    with get_db() as db:
        rows = db.query(MomentORM).filter(
            MomentORM.image_url.like("%picsum%")
        ).all()
        moments = [{"id": r.id, "caption": r.caption} for r in rows]

    if not moments:
        print("没有使用 picsum 占位图的朋友圈")
        return

    print(f"找到 {len(moments)} 条使用 picsum 占位图的朋友圈")
    success = 0
    failed = 0

    for i, m in enumerate(moments, 1):
        prompt, img_style = generate_moment_image_prompt(m["caption"])
        full_prompt = augment_prompt_with_style(prompt, img_style)
        try:
            url = generate_image_with_volcano(full_prompt, width=1024, height=1024)
            if not url:
                failed += 1
                print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 火山引擎返回空")
                continue

            key = hashlib.md5(full_prompt.encode("utf-8")).hexdigest()
            cache_path = IMAGE_CACHE_DIR / f"{key}.png"

            if _download_image(url, str(cache_path)):
                if cache_path.exists() and cache_path.stat().st_size > 1000:
                    local_url = _to_local_image_url(cache_path)
                    with get_db() as db:
                        r = db.query(MomentORM).filter(MomentORM.id == m["id"]).first()
                        if r:
                            r.image_url = local_url
                    success += 1
                    print(f"  [{i}/{len(moments)}] ✓ 朋友圈 #{m['id']} 配图已更新 -> {local_url}")
                else:
                    failed += 1
                    print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 文件过小")
            else:
                failed += 1
                print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 下载失败")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(moments)}] ✗ 朋友圈 #{m['id']} 失败: {e}")

        time.sleep(1)

    print(f"\n完成: 成功 {success} 个, 失败 {failed} 个")


if __name__ == "__main__":
    main()
