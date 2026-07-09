#!/usr/bin/env python3
"""
使用 x.ai 图片生成 API 重新生成所有朋友圈配图。
"""

import sys
import os
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import get_db, ConfigGroupORM, MomentORM
from services.image_generation import augment_prompt_with_style, generate_moment_image_prompt
import urllib.request
import urllib.error


def get_xai_config():
    with get_db() as db:
        cfg = db.query(ConfigGroupORM).filter(ConfigGroupORM.key == "xai_image_gen").first()
        if not cfg:
            raise RuntimeError("未找到 xai_image_gen 配置组")
        return cfg.config_json or {}


def generate_image_with_xai(prompt: str, config: dict) -> str:
    """调用 x.ai images/generations API 生成图片，返回图片 URL"""
    api_key = config.get("api_key", "")
    model = config.get("model", "grok-2-image")
    base_url = config.get("base_url", "https://api.x.ai/v1/images/generations")

    if not api_key:
        raise RuntimeError("x.ai API Key 未配置")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "url",
    }).encode("utf-8")

    req = urllib.request.Request(base_url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # OpenAI-compatible response format
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0].get("url", "")
            raise RuntimeError(f"Unexpected response: {data}")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        raise RuntimeError(f"HTTP {e.code}: {err_body}")


def main():
    config = get_xai_config()
    print(f"[Config] provider={config.get('provider')} model={config.get('model')} url={config.get('base_url')}")

    with get_db() as db:
        moments = db.query(MomentORM).filter(
            MomentORM.caption.isnot(None),
            MomentORM.caption != ""
        ).all()
        moments_data = [
            {"id": m.id, "caption": m.caption, "image_url": m.image_url or ""}
            for m in moments
        ]

    print(f"[INFO] 共 {len(moments_data)} 条朋友圈需要重新生成配图")
    success = 0
    failed = 0

    for i, m in enumerate(moments_data, 1):
        prompt, img_style = generate_moment_image_prompt(m["caption"])
        try:
            api_prompt = augment_prompt_with_style(prompt, img_style)
            new_url = generate_image_with_xai(api_prompt, config)
            if new_url:
                with get_db() as db:
                    r = db.query(MomentORM).filter(MomentORM.id == m["id"]).first()
                    if r:
                        r.image_url = new_url
                success += 1
                print(f"  [{i}/{len(moments_data)}] ✓ 朋友圈 #{m['id']} 配图已生成")
            else:
                failed += 1
                print(f"  [{i}/{len(moments_data)}] ✗ 朋友圈 #{m['id']} 生成失败（空URL）")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(moments_data)}] ✗ 朋友圈 #{m['id']} 失败: {e}")

        # 避免请求过快
        time.sleep(0.5)

    print(f"\n完成: 成功 {success} 个, 失败 {failed} 个")


if __name__ == "__main__":
    main()
