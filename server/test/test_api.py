"""快速测试 LLM API 连通性"""
import os
import sys

# 加载 .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

# 把 server 目录加入 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.agent import get_llm, test_llm_connection


def main():
    print("=" * 50)
    print("LLM API 连通性测试")
    print("=" * 50)

    # 检查环境变量
    provider = os.getenv("MODEL_PROVIDER", "google").lower()
    print(f"\n当前 provider: {provider}")

    if provider in ("google", "gemini"):
        key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GOOGLEAI_API_KEY", "")
        print(f"GOOGLE_API_KEY: {'已设置 (' + key[:8] + '...)' if key else '❌ 未设置'}")
        print(f"模型: {os.getenv('GEMINI_TEXT_MODEL', 'gemini-flash-latest')}")
    elif provider == "deepseek":
        key = os.getenv("DEEPSEEK_API_KEY", "")
        print(f"DEEPSEEK_API_KEY: {'已设置 (' + key[:8] + '...)' if key else '❌ 未设置'}")
        print(f"模型: deepseek-v4-flash")
        print(f"地址: https://dashscope.aliyuncs.com/compatible-mode/v1")
    elif provider in ("grok", "xai"):
        key = os.getenv("XAI_API_KEY", "")
        print(f"XAI_API_KEY: {'已设置 (' + key[:8] + '...)' if key else '❌ 未设置'}")
        print(f"模型: grok-3-latest")
    else:
        print(f"未知/已下线 provider: {provider}（将自动落到 google）")

    print("\n" + "-" * 50)
    print("发送测试消息: '你好'")
    print("-" * 50)

    result = test_llm_connection(provider=provider)

    if result["ok"]:
        print(f"\n✅ 连接成功!")
        print(f"回复: {result['response']}")
    else:
        print(f"\n❌ 连接失败!")
        print(f"错误: {result['error']}")

    print("\n" + "=" * 50)


if __name__ == "__main__":
    main()
