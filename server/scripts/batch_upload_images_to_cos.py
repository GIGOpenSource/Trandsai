#!/usr/bin/env python3
"""
批量将 data/images/ 目录中的图片上传到腾讯云COS。
用法: python scripts/batch_upload_images_to_cos.py [--dry-run]
"""

import sys
import os
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from services.cos_storage import is_cos_enabled, upload_file_to_cos
from core.config import BASE_DIR


def main():
    dry_run = "--dry-run" in sys.argv

    if not is_cos_enabled():
        print("❌ COS 未配置，请检查 .env 文件中的 COS_SECRET_ID, COS_SECRET_KEY, COS_BUCKET 等配置")
        sys.exit(1)

    image_dir = Path(BASE_DIR) / "data" / "images"
    if not image_dir.exists():
        print(f"❌ 图片目录不存在: {image_dir}")
        sys.exit(1)

    # 获取所有图片文件
    image_files = [
        f for f in image_dir.iterdir()
        if f.is_file() and f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp')
    ]

    print(f"找到 {len(image_files)} 个图片文件")
    if dry_run:
        print("(DRY RUN 模式，不会实际上传)")
        for f in image_files[:10]:
            print(f"  - {f.name}")
        if len(image_files) > 10:
            print(f"  ... 还有 {len(image_files) - 10} 个文件")
        return

    success = 0
    failed = 0
    skipped = 0

    for i, img_path in enumerate(image_files, 1):
        cos_key = f"images/{img_path.name}"
        try:
            url = upload_file_to_cos(str(img_path), cos_key)
            if url:
                success += 1
                print(f"  [{i}/{len(image_files)}] ✓ {img_path.name} -> {url[:80]}...")
            else:
                failed += 1
                print(f"  [{i}/{len(image_files)}] ✗ {img_path.name} 上传返回 None")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(image_files)}] ✗ {img_path.name} 异常: {e}")

        # 每上传10个文件暂停一下，避免触发限流
        if i % 10 == 0:
            time.sleep(0.5)

    print(f"\n完成: 成功 {success} 个, 失败 {failed} 个, 总共 {len(image_files)} 个")


if __name__ == "__main__":
    main()
