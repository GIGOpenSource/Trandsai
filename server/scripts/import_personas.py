import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from services.companion_manager import CompanionManager

# 加载预设人设
personas_path = Path(__file__).parent.parent / "data" / "personas" / "zh_cn.json"
with open(personas_path, "r", encoding="utf-8") as f:
    personas = json.load(f)

print(f"加载了 {len(personas)} 个预设人设")

# 创建 CompanionManager
cm = CompanionManager()
print(f"当前已有 {len(cm._companions)} 个伴侣")

# 逐个导入
created = 0
skipped = 0
for p in personas:
    companion = cm.create(p)
    # 如果返回的伴侣ID和传入的不一致，说明是已存在的（去重）
    if companion.profile.name == p["name"] and companion.profile.city == p["city"]:
        # 检查是否真的是新创建的（通过比较字段完整性）
        # 简单判断：如果hobbies为空，说明是旧的测试数据，更新它
        if not companion.profile.hobbies:
            cm.update(companion.profile.id, p)
            print(f"  更新: {p['name']} ({p['city']})")
            created += 1
        else:
            print(f"  已存在: {p['name']} ({p['city']}) - 跳过")
            skipped += 1
    else:
        print(f"  创建: {p['name']} ({p['city']})")
        created += 1

print(f"\n导入完成: 新增/更新 {created} 个, 跳过 {skipped} 个")
print(f"数据库中伴侣总数: {len(cm._companions)}")
