#!/usr/bin/env python3
"""
图片理解主入口脚本
支持图片描述、OCR文字提取、物体识别、图片问答
"""

import argparse
import json
import os
import sys
from pathlib import Path

from image_understander import ImageUnderstander


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="图片理解工具 - 基于 OpenAI GPT-4 Vision",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 图片描述
  python main.py -i photo.jpg -m describe
  
  # 提取文字
  python main.py -i screenshot.png -m ocr
  
  # 识别物体
  python main.py -i photo.jpg -m objects
  
  # 图片问答
  python main.py -i photo.jpg -m qa -q "这个图片里有什么？"
        """
    )
    
    parser.add_argument("-i", "--image", required=True, help="图片路径")
    parser.add_argument("-m", "--mode", default="describe", 
                        choices=["describe", "ocr", "objects", "qa"],
                        help="理解模式 (默认: describe)")
    parser.add_argument("-a", "--api-key", help="OpenAI API Key")
    parser.add_argument("-q", "--prompt", default="请详细描述这张图片",
                        help="问答模式的问题 (默认: 请详细描述这张图片)")
    parser.add_argument("-o", "--output", help="输出文件路径 (JSON格式)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()
    
    # 检查图片文件
    if not os.path.exists(args.image):
        print(f"❌ 错误: 图片文件不存在: {args.image}")
        sys.exit(1)
    
    # 获取 API Key
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("❌ 错误: 请设置 OpenAI API Key")
        print("   使用方式:")
        print("   1. 环境变量: set OPENAI_API_KEY=sk-your-key")
        print("   2. 命令行参数: python main.py -a sk-your-key -i photo.jpg")
        sys.exit(1)
    
    # 初始化理解器
    understander = ImageUnderstander(api_key, verbose=args.verbose)
    
    # 根据模式调用
    print(f"🔍 正在分析图片: {args.image}")
    print(f"📋 模式: {args.mode}")
    print("-" * 50)
    
    if args.mode == "describe":
        result = understander.describe(args.image)
        print(result)
    elif args.mode == "ocr":
        result = understander.extract_text(args.image)
        print(result)
    elif args.mode == "objects":
        result =understander.identify_objects(args.image)
        print(result)
    elif args.mode == "qa":
        result = understander.answer_question(args.image, args.prompt)
        print(result)
    
    # 保存结果
    if args.output:
        output_data = {
            "mode": args.mode,
            "image": args.image,
            "result": result
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n💾 结果已保存到: {args.output}")


if __name__ == "__main__":
    main()

