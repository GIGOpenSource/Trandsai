#!/usr/bin/env python3
"""
按 BATCH_GENERATION_ALL_LANGS_ORDER（与 API「全部语言」一致：含 zh 与 id）依次生成智能体。
**默认每个语言创建10个，可通过 BATCH_COUNT_PER_LANG=20 环境变量调整**。
**SKIP_IMAGE_GEN=1 默认启用，避免批量生成时图片API token爆炸和长时间等待**。
所有机器人资料必须完整、丰富，符合提示词对所有字段的要求。
优化后：LLM调用已异步化，不会阻塞事件循环。
"""

import sys
import os
import asyncio
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from core.state import set_companion_manager
from services.companion_manager import CompanionManager
from api.admin import _batch_generate_all_langs_core
from core.database import init_db
from services.culture_data import BATCH_GENERATION_ALL_LANGS_ORDER

# 初始化数据库和 CompanionManager
init_db()
cm = CompanionManager()
set_companion_manager(cm)
print(f"CompanionManager 已初始化，当前已有 {len(cm.list_all())} 个智能体")


async def main():
    # 优化默认值：小批量 + 默认跳过图片生成（大幅提升速度，专注高质量profile）
    # 可通过环境变量覆盖: BATCH_COUNT_PER_LANG=20 SKIP_IMAGE_GEN=0
    os.environ.setdefault("SKIP_IMAGE_GEN", "1")  # 本脚本默认跳过图片，防止 token 消耗过大
    count_per_lang = int(os.getenv("BATCH_COUNT_PER_LANG", "10"))
    skip_image = os.getenv("SKIP_IMAGE_GEN", "0") in ("1", "true", "yes")
    valid_langs = list(BATCH_GENERATION_ALL_LANGS_ORDER)
    total_all = count_per_lang * len(valid_langs)

    # 检查关键 API Key
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    model_provider = os.getenv("MODEL_PROVIDER", "anthropic")
    if not anthropic_key and model_provider != "deepseek":
        print("⚠️  WARNING: ANTHROPIC_API_KEY 未设置，LLM生成可能失败！")
        print(f"   当前 MODEL_PROVIDER={model_provider}，建议在 .env 中配置 ANTHROPIC_API_KEY 或 DEEPSEEK_API_KEY")
    if skip_image:
        print("🖼️  SKIP_IMAGE_GEN=1 已启用（本脚本默认），跳过头像/朋友圈图片生成（加速 5-10x）")
    print(f"并发图片限制: MAX_CONCURRENT_IMAGES={os.getenv('MAX_CONCURRENT_IMAGES', '5')}")
    print(f"LLM Provider: {model_provider}")

    print("=" * 60)
    print(f"批量创建指定语言智能体: {len(valid_langs)} 种语言{valid_langs} × {count_per_lang} 个 = {total_all} 个")
    print("与 admin 批量「全部语言」同序；每语言资料须完整且与词库/文化语境一致。")
    print("已通过 init_db() 确保默认配置（image_generation / agent）已插入")
    print("优化点: LLM调用异步化 + 默认跳过图片生成 + 更好的错误提示")
    if skip_image:
        print("⚡ 图片生成已跳过 - 专注于高质量 profile 创建（推荐批量使用）")
    print("=" * 60)

    created_all = 0
    try:
        async for event in _batch_generate_all_langs_core({
            "count_per_lang": count_per_lang,
        }):
            data = json.loads(event)
            event_type = data.get("type")

            if event_type == "lang_start":
                lang = data.get("lang", "")
                print(f"\n[开始] 语言: {lang} ({data.get('lang_index')}/{data.get('total_langs')})")

            elif event_type == "progress":
                created_all = data.get("created_all", 0)
                total = data.get("total_all", total_all)
                lang = data.get("lang", "")
                current = data.get("current_lang", 0)
                total_lang = data.get("total_lang", count_per_lang)
                print(f"  [{lang}] 进度: {current}/{total_lang} | 总计: {created_all}/{total}")

            elif event_type == "error":
                print(f"  [错误] 语言: {data.get('lang')}, 批次: {data.get('batch')}, 消息: {data.get('message')}")

            elif event_type == "complete":
                created_all = data.get("created_all", 0)
                print(f"\n[完成] 总共创建: {created_all}/{total_all}")

    except Exception as e:
        print(f"\n[致命错误] 批量生成失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("批量创建完成!")
    print(f"  成功创建: {created_all} 个智能体")
    print(f"  当前数据库中共有: {len(CompanionManager().list_all())} 个智能体")
    print("提示：如遇 LLM 错误，请设置 ANTHROPIC_API_KEY 或其他 MODEL_PROVIDER")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
