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
    provider = os.getenv("MODEL_PROVIDER", "deepseek").lower()
    print(f"\n当前 provider: {provider}")

    if provider == "deepseek":
        key = os.getenv("DEEPSEEK_API_KEY", "")
        print(f"DEEPSEEK_API_KEY: {'已设置 (' + key[:8] + '...)' if key else '❌ 未设置'}")
        print(f"模型: qwen3.7-max-2026-06-08")
        print(f"地址: https://dashscope.aliyuncs.com/compatible-mode/v1")
    elif provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        print(f"OPENAI_API_KEY: {'已设置 (' + key[:8] + '...)' if key else '❌ 未设置'}")
    elif provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        print(f"ANTHROPIC_API_KEY: {'已设置 (' + key[:8] + '...)' if key else '❌ 未设置'}")

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
