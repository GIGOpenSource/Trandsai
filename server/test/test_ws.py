"""WebSocket 对话测试"""
import asyncio
import json
import sys
import os

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import websockets


async def test_ws():
    # 先拿 token
    import requests
    base = "http://127.0.0.1:8000"

    # 用管理员登录拿 token
    print("1. 登录获取 token...")
    try:
        resp = requests.post(f"{base}/api/auth/login", json={
            "username": "liangwater@163.com",
            "password": "123456"
        })
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            print(f"   登录返回: {data}")
            print("   ❌ 拿不到 token，跳过登录，用无 token 测试")
            token = None
        else:
            print(f"   ✅ token: {token[:16]}...")
    except Exception as e:
        print(f"   ❌ 登录失败: {e}")
        token = None

    # 获取一个 companion_id
    print("\n2. 获取伴侣列表...")
    try:
        headers = {"x-token": token} if token else {}
        resp = requests.get(f"{base}/companions?filter_type=all", headers=headers)
        companions = resp.json()
        if not companions:
            print("   ❌ 没有伴侣，无法测试")
            return
        companion_id = companions[0]["id"]
        name = companions[0].get("profile", {}).get("name", "?")
        print(f"   ✅ 使用: {name} ({companion_id})")
    except Exception as e:
        print(f"   ❌ 获取失败: {e}")
        return

    # 建立 WebSocket 连接
    ws_url = f"ws://127.0.0.1:8000/ws/chat/{companion_id}?lang=zh"
    if token:
        ws_url += f"&token={token}"

    print(f"\n3. 连接 WebSocket: {ws_url[:80]}...")
    try:
        async with websockets.connect(ws_url, ping_timeout=30) as ws:
            print("   ✅ 连接成功")

            # 发送测试消息
            test_msg = "你好，测试一下"
            print(f"\n4. 发送消息: {test_msg}")
            await ws.send(json.dumps({"text": test_msg}))

            # 接收回复
            print("\n5. 等待回复...")
            timeout_count = 0
            max_wait = 60  # 最多等 60 秒
            while timeout_count < max_wait:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(msg)
                    msg_type = data.get("type", "?")
                    text = data.get("text", "")

                    if msg_type == "typing":
                        print("   ⌨️  对方正在输入...")
                    elif msg_type == "message":
                        print(f"   💬 回复: {text}")
                    elif msg_type == "toast":
                        print(f"   💭 思考: {text[:80]}...")
                    elif msg_type == "filler":
                        print(f"   ⏳ 等待中: {text}")
                    elif msg_type == "error":
                        print(f"   ❌ 错误: {text}")
                        break
                    elif msg_type == "system":
                        print(f"   ℹ️  系统: {text}")
                    else:
                        print(f"   📦 [{msg_type}]: {text[:100]}")

                except asyncio.TimeoutError:
                    timeout_count += 1
                    if timeout_count % 10 == 0:
                        print(f"   ⏰ 已等待 {timeout_count} 秒...")
                    continue

            if timeout_count >= max_wait:
                print(f"\n   ⚠️ 等待 {max_wait} 秒无回复，可能超时了")

    except Exception as e:
        print(f"   ❌ WebSocket 连接失败: {e}")


if __name__ == "__main__":
    asyncio.run(test_ws())
