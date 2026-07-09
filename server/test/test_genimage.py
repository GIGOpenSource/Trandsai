import json
import requests

# 阿里云百炼密钥
DASHSCOPE_API_KEY = "sk-f6ea7e4bdd35459ba0b93dcd659b8744"
# 裸域名标准多模态文生图接口（官方推荐，不会报url error）
API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
# 你的文生图完整模型标识
MODEL_NAME = "qwen-image-2.0-pro-2026-04-22"

def alibaba_text_to_image(prompt: str, width: int = 1024, height: int = 1024):
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }
    # 千问图像标准请求体
    payload = {
        "model": MODEL_NAME,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
        },
        "parameters": {
            "size": f"{width}*{height}",
            "n": 1
        }
    }
    try:
        print(f"请求完整地址: {API_URL}")
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        print(f"HTTP状态码: {resp.status_code}")
        resp.raise_for_status()
        res_data = resp.json()
        print("完整返回数据：")
        print(json.dumps(res_data, indent=2, ensure_ascii=False))

        # 解析图片url
        output = res_data.get("output", {})
        results = output.get("choices", [])
        if results and len(results) > 0:
            content_list = results[0].get("message", {}).get("content", [])
            img_url = None
            for item in content_list:
                if item.get("type") == "image":
                    img_url = item.get("image_url")
                    break
            if img_url:
                print(f"\n✅ 生成成功，图片临时链接：\n{img_url}")
                return img_url
        print("❌ 接口返回无图片结果")
        return None
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 请求异常：{str(e)}")
        if hasattr(e, "response") and e.response is not None:
            print(f"错误响应内容：{e.response.text}")
        return None


if __name__ == "__main__":
    test_prompt = "二次元少女，海边日落，治愈插画，高质量，柔和光影"
    alibaba_text_to_image(prompt=test_prompt, width=1024, height=1024)