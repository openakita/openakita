#!/usr/bin/env python3
"""
Image Understanding using Dashscope (Qwen Vision Models)

This script enables AI to understand and analyze images using Dashscope's
vision API models (qwen-vl-plus, qwen-vl-max).

Usage:
    python image_understanding.py --image path/to/image.jpg
    python image_understanding.py --image https://example.com/image.png --prompt "图片里有什么？"
    python image_understanding.py --image ./screenshot.png --extract-text --describe
"""

import argparse
import json
import os
import sys
from typing import Optional, Dict, Any
from pathlib import Path


# Dashscope 配置
DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-vl-plus"


def validate_image_path(image_path: str) -> str:
    """
    Validate and normalize image path or URL.
    
    Args:
        image_path: Path to local image or URL
        
    Returns:
        Validated image path
        
    Raises:
        ValueError: If image path is invalid or file doesn't exist
    """
    if image_path.startswith(('http://', 'https://')):
        if not image_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')):
            raise ValueError(f"Invalid image URL format: {image_path}")
        return image_path
    
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"Image file not found: {image_path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {image_path}")
    
    valid_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
    if path.suffix.lower() not in valid_extensions:
        raise ValueError(f"Invalid image format: {path.suffix}. Supported: {', '.join(valid_extensions)}")
    
    return str(path.absolute())


def encode_image(image_path: str) -> str:
    """
    Encode image to base64 string.
    
    Args:
        image_path: Path to image file
        
    Returns:
        Base64 encoded image string
    """
    import base64
    
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')


def call_dashscope_api(api_key: str, image_path: str, prompt: str, model: str) -> Dict[str, Any]:
    """
    Call Dashscope Vision API to analyze image.
    
    Args:
        api_key: Dashscope API key
        image_path: Path to image file or URL
        prompt: Custom prompt for analysis
        model: Model name (qwen-vl-plus or qwen-vl-max)
        
    Returns:
        API response as dictionary
    """
    import requests
    
    # Prepare image content
    if image_path.startswith(('http://', 'https://')):
        image_content = {
            "type": "image_url",
            "image_url": {"url": image_path}
        }
    else:
        base64_image = encode_image(image_path)
        # Detect mime type
        ext = Path(image_path).suffix.lower()
        mime_type = "image/jpeg"
        if ext == '.png':
            mime_type = "image/png"
        elif ext == '.gif':
            mime_type = "image/gif"
        elif ext == '.webp':
            mime_type = "image/webp"
        
        image_content = {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}"
            }
        }
    
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                image_content
            ]
        }
    ]
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1500,
        "temperature": 0.1
    }
    
    try:
        response = requests.post(
            f"{DASHSCOPE_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=90
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        error_msg = f"API request failed: {e.response.text}"
        try:
            error_data = e.response.json()
            if 'error' in error_data:
                error_msg = f"API error: {error_data['error'].get('message', str(error_data))}"
        except:
            pass
        raise Exception(error_msg)
    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error: {str(e)}")


def analyze_image(
    api_key: str,
    image_path: str,
    custom_prompt: Optional[str] = None,
    describe: bool = True,
    extract_text: bool = False,
    identify_objects: bool = False,
    model: str = DEFAULT_MODEL
) -> Dict[str, Any]:
    """
    Analyze image with specified analysis types.
    
    Args:
        api_key: Dashscope API key
        image_path: Path to image file or URL
        custom_prompt: Optional custom prompt
        describe: Whether to describe the image
        extract_text: Whether to extract text from image
        identify_objects: Whether to identify objects
        model: Model to use
        
    Returns:
        Analysis results as dictionary
    """
    # Build prompt based on analysis type
    if custom_prompt:
        prompt = custom_prompt
    else:
        tasks = []
        if describe:
            tasks.append("详细描述这张图片的内容，包括物体、人物、场景、颜色和整体构成")
        if extract_text:
            tasks.append("提取图片中所有可见的文字(OCR)")
        if identify_objects:
            tasks.append("识别并列出图片中所有可辨认的物体、人物和元素")
        
        if tasks:
            prompt = f"""请分析这张图片，提供以下信息：
{'；'.join(tasks)}

请用清晰的分段格式回答。"""
        else:
            prompt = "请对这张图片进行全面详细的描述，包括所有可见的物体、人物、场景、文字和任何值得注意的细节。"
    
    # Call API
    response = call_dashscope_api(api_key, image_path, prompt, model)
    
    # Parse response
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    
    # Extract usage info
    usage = response.get("usage", {})
    
    return {
        "success": True,
        "image_path": image_path,
        "model": model,
        "api_provider": "dashscope",
        "analysis": {
            "description": content if describe else None,
            "extracted_text": content if extract_text else None,
            "objects": content if identify_objects else None,
            "full_response": content
        },
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0)
        }
    }


def execute(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute the image understanding analysis."""
    # Get API key - 支持 DASHSCOPE_API_KEY 或 OPENAI_API_KEY
    api_key = args.api_key or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "API key not provided. Use --api-key or set DASHSCOPE_API_KEY environment variable"
        )
    
    # Validate image
    image_path = validate_image_path(args.image)
    
    # Determine analysis type
    describe = not (args.extract_text or args.identify_objects or args.custom_prompt)
    extract_text = args.extract_text
    identify_objects = args.identify_objects
    custom_prompt = args.custom_prompt
    model = args.model or DEFAULT_MODEL
    
    # Analyze
    result = analyze_image(
        api_key=api_key,
        image_path=image_path,
        custom_prompt=custom_prompt,
        describe=describe,
        extract_text=extract_text,
        identify_objects=identify_objects,
        model=model
    )
    
    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="使用 Dashscope（通义千问）视觉模型分析图片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 基本图片描述
    python image_understanding.py --image photo.jpg
    
    # 提取图片中的文字
    python image_understanding.py --image screenshot.png --extract-text
    
    # 识别图片中的物体
    python image_understanding.py --image photo.jpg --identify-objects
    
    # 自定义分析提示词
    python image_understanding.py --image photo.jpg --prompt "这个产品多少钱？"
    
    # 使用环境变量中的 API key
    export DASHSCOPE_API_KEY=your_key
    python image_understanding.py --image photo.jpg
    
    # 使用网络图片URL
    python image_understanding.py --image "https://example.com/photo.jpg" --describe
    
    # 使用更强的模型
    python image_understanding.py --image photo.jpg --model qwen-vl-max

环境变量:
    DASHSCOPE_API_KEY    你的 Dashscope API key
    OPENAI_API_KEY       也可以使用（兼容性支持）
        """
    )
    
    # Required
    parser.add_argument(
        "--image", "-i",
        required=True,
        help="本地图片路径或图片URL"
    )
    
    # Optional
    parser.add_argument(
        "--api-key",
        help="Dashscope API key (也可通过 DASHSCOPE_API_KEY 环境变量设置)"
    )
    
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        choices=["qwen-vl-plus", "qwen-vl-max"],
        help=f"使用的模型 (默认: {DEFAULT_MODEL})"
    )
    
    parser.add_argument(
        "--custom-prompt", "-p",
        help="自定义图片分析提示词"
    )
    
    # Analysis type
    analysis_group = parser.add_mutually_exclusive_group()
    analysis_group.add_argument(
        "--describe",
        action="store_true",
        default=True,
        help="描述图片内容 (默认行为)"
    )
    analysis_group.add_argument(
        "--extract-text", "-e",
        action="store_true",
        help="从图片提取文字 (OCR)"
    )
    analysis_group.add_argument(
        "--identify-objects", "-o",
        action="store_true",
        help="识别图片中的物体"
    )
    
    # Output
    parser.add_argument(
        "--compact",
        action="store_true",
        help="输出紧凑JSON (不缩进)"
    )
    
    args = parser.parse_args()
    
    try:
        result = execute(args)
        indent = None if args.compact else 2
        print(json.dumps(result, ensure_ascii=False, indent=indent))
        sys.exit(0)
        
    except ValueError as e:
        error_result = {
            "success": False,
            "error": {
                "type": "validation_error",
                "message": str(e)
            }
        }
        print(json.dumps(error_result, ensure_ascii=False, indent=2))
        sys.exit(2)
        
    except Exception as e:
        error_result = {
            "success": False,
            "error": {
                "type": "execution_error",
                "message": str(e)
            }
        }
        print(json.dumps(error_result, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()

