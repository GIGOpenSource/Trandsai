import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 加载 .env 文件
from dotenv import load_dotenv
load_dotenv(project_root / ".env")

# 现在可以导入 agent 模块
from server.services.agent import get_llm
from langchain_core.messages import HumanMessage


async def test_grok():
    print("=" * 50)
    print("测试 Grok API 连接")
    print("=" * 50)

    # 检查环境变量
    api_key = os.getenv("XAI_API_KEY", "")
    model_provider = os.getenv("MODEL_PROVIDER", "")

    print(f"MODEL_PROVIDER: {model_provider}")
    print(f"XAI_API_KEY: {api_key[:20]}..." if api_key else "XAI_API_KEY: 未设置")
    print()

    if not api_key:
        print("❌ 错误: XAI_API_KEY 未设置")
        return

    try:
        print("正在初始化 Grok LLM...")
        llm = get_llm(provider="grok")
        print("✅ LLM 初始化成功")

        print("\n正在发送测试消息...")
        test_messages = ["你好", "Hello", "こんにちは"]

        for msg in test_messages:
            print(f"\n发送: {msg}")
            resp = llm.invoke([HumanMessage(content=msg)])
            content = resp.content if hasattr(resp, 'content') else str(resp)
            print(f"回复: {content[:100]}...")
            print("-" * 30)

        print("\n✅ Grok API 测试成功！")

    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_grok())
