"""
图片理解模块 - 使用 Dashscope Qwen-VL 模型
"""
import os
import base64
import json
import requests

# 配置
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not API_KEY:
    print("错误: 请设置 DASHSCOPE_API_KEY 环境变量")
    exit(1)

BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


def encode_image(image_path):
    """将图片编码为base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def call_dashscope(messages, model="qwen-vl-plus", max_tokens=1000):
    """调用Dashscope API"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "input": {
            "messages": messages
        },
        "parameters": {
            "max_tokens": max_tokens
        }
    }

    response = requests.post(BASE_URL, headers=headers, json=payload)
    result = response.json()

    if "output" in result and "choices" in result["output"]:
        return result["output"]["choices"][0]["message"]["content"]
    else:
        return f"错误: {json.dumps(result, ensure_ascii=False, indent=2)}"


def describe_image(image_path, model="qwen-vl-plus"):
    """描述图片内容"""
    image_b64 = encode_image(image_path)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请详细描述这张图片的内容，包括场景、人物、物体、颜色等所有细节。"
                },
                {
                    "type": "image",
                    "image": f"data:image/jpeg;base64,{image_b64}"
                }
            ]
        }
    ]

    return call_dashscope(messages, model, max_tokens=1000)


def extract_text(image_path, model="qwen-vl-plus"):
    """提取图片中的文字 (OCR)"""
    image_b64 = encode_image(image_path)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请提取图片中的所有文字，保持原格式，不要遗漏任何内容。"
                },
                {
                    "type": "image",
                    "image": f"data:image/jpeg;base64,{image_b64}"
                }
            ]
        }
    ]

    return call_dashscope(messages, model, max_tokens=2000)


def identify_objects(image_path, model="qwen-vl-plus"):
    """识别图片中的物体"""
    image_b64 = encode_image(image_path)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请列出图片中的所有物体、人物、元素，用清晰的列表格式。"
                },
                {
                    "type": "image",
                    "image": f"data:image/jpeg;base64,{image_b64}"
                }
            ]
        }
    ]

    return call_dashscope(messages, model, max_tokens=500)


def answer_question(image_path, question, model="qwen-vl-plus"):
    """回答关于图片的问题"""
    image_b64 = encode_image(image_path)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": question
                },
                {
                    "type": "image",
                    "image": f"data:image/jpeg;base64,{image_b64}"
                }
            ]
        }
    ]

    return call_dashscope(messages, model, max_tokens=500)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法:")
        print("  python image_understander.py -i <图片路径> -m describe   # 描述图片")
        print("  python image_understander.py -i <图片路径> -m ocr        # 提取文字")
        print("  python image_understander.py -i <图片路径> -m objects    # 识别物体")
        print("  python image_understander.py -i <图片路径> -m qa -q '问题'  # 图片问答")
        sys.exit(1)

    image_path = sys.argv[2]

    if "-m" in sys.argv:
        mode = sys.argv[sys.argv.index("-m") + 1]
    else:
        mode = "describe"

    if "-q" in sys.argv:
        question = sys.argv[sys.argv.index("-q") + 1]
    else:
        question = None

    if mode == "describe":
        print(describe_image(image_path))
    elif mode == "ocr":
        print(extract_text(image_path))
    elif mode == "objects":
        print(identify_objects(image_path))
    elif mode == "qa":
        if question:
            print(answer_question(image_path, question))
        else:
            print("错误: 请使用 -q 指定问题")
    else:
        print(f"未知模式: {mode}")

