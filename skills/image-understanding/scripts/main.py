#!/usr/bin/env python3
"""
image-understanding - Enable AI to understand and analyze images using vision API

This script allows users to analyze images by calling vision API (like OpenAI GPT-4 Vision).
It can describe image content, extract text, identify objects, and answer questions about images.

Usage:
    python image_understanding.py --image path/to/image.jpg
    python image_understanding.py --image https://example.com/image.png --prompt "What objects are in this image?"
    python image_understanding.py --image ./screenshot.png --extract-text --describe
"""

import argparse
import json
import os
import sys
from typing import Optional, Dict, Any, List
from pathlib import Path


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
        # URL validation
        if not image_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            raise ValueError(f"Invalid image URL format: {image_path}")
        return image_path
    
    # Local file path
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"Image file not found: {image_path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {image_path}")
    
    # Check file extension
    valid_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    if path.suffix.lower() not in valid_extensions:
        raise ValueError(f"Invalid image format: {path.suffix}. Supported formats: {', '.join(valid_extensions)}")
    
    return str(path.absolute())


def encode_image(image_path: str) -> str:
    """
    Encode image to base64 string for API upload.
    
    Args:
        image_path: Path to image file
        
    Returns:
        Base64 encoded image string
        
    Raises:
        Exception: If encoding fails
    """
    import base64
    
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return encoded_string
    except Exception as e:
        raise Exception(f"Failed to encode image: {str(e)}")


def call_vision_api(api_key: str, image_path: str, prompt: str, model: str) -> Dict[str, Any]:
    """
    Call OpenAI Vision API to analyze image.
    
    Args:
        api_key: OpenAI API key
        image_path: Path to image file or URL
        prompt: Custom prompt for analysis
        model: Model name to use
        
    Returns:
        API response as dictionary
        
    Raises:
        Exception: If API call fails
    """
    import requests
    
    # Prepare image content
    if image_path.startswith(('http://', 'https://')):
        image_content = {"type": "image_url", "image_url": {"url": image_path}}
    else:
        base64_image = encode_image(image_path)
        image_content = {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{base64_image}"
            }
        }
    
    # Build messages
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
        "max_tokens": 1000
    }
    
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
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
    model: str = "gpt-4-vision-preview"
) -> Dict[str, Any]:
    """
    Analyze image with specified analysis types.
    
    Args:
        api_key: OpenAI API key
        image_path: Path to image file or URL
        custom_prompt: Optional custom prompt
        describe: Whether to describe the image
        extract_text: Whether to extract text from image
        identify_objects: Whether to identify objects in image
        model: Model to use for analysis
        
    Returns:
        Analysis results as dictionary
    """
    # Build analysis prompt
    analysis_tasks = []
    
    if describe:
        analysis_tasks.append("Describe the image in detail, including objects, people,场景, colors, and overall composition")
    
    if extract_text:
        analysis_tasks.append("Extract all visible text from the image (OCR)")
    
    if identify_objects:
        analysis_tasks.append("Identify and list all recognizable objects, people, and elements in the image")
    
    if custom_prompt:
        prompt = custom_prompt
    else:
        prompt = f"""Please analyze this image and provide the following information:
1. {'Describe the image content in detail' if describe else ''}
2. {'Extract all visible text from the image' if extract_text else ''}
3. {'List all identifiable objects and elements' if identify_objects else ''}

Please format your response as a structured analysis with clear sections."""
    
    # Remove empty tasks
    analysis_tasks = [task for task in analysis_tasks if task]
    
    if not analysis_tasks and not custom_prompt:
        # Default: full analysis
        prompt = "Provide a comprehensive description of this image, including all visible objects, people,场景, text, and any notable details."
    elif not analysis_tasks and custom_prompt:
        prompt = custom_prompt
    
    # Call API
    response = call_vision_api(api_key, image_path, prompt, model)
    
    # Parse response
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    
    # Extract usage information if available
    usage = response.get("usage", {})
    
    return {
        "success": True,
        "image_path": image_path,
        "model": model,
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
    """
    Execute the image understanding analysis.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Analysis result as dictionary
    """
    # Get API key
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("API key not provided. Use --api-key or set OPENAI_API_KEY environment variable")
    
    # Validate image path
    image_path = validate_image_path(args.image)
    
    # Determine analysis type
    describe = not (args.extract_text or args.identify_objects or args.custom_prompt)
    extract_text = args.extract_text
    identify_objects = args.identify_objects
    custom_prompt = args.custom_prompt
    
    # Perform analysis
    result = analyze_image(
        api_key=api_key,
        image_path=image_path,
        custom_prompt=custom_prompt,
        describe=describe,
        extract_text=extract_text,
        identify_objects=identify_objects,
        model=args.model
    )
    
    return result


def main():
    """Main entry point for the image understanding script."""
    parser = argparse.ArgumentParser(
        description="Analyze images using AI vision capabilities (OpenAI GPT-4 Vision)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic image description
    python image_understanding.py --image photo.jpg
    
    # Extract text from image
    python image_understanding.py --image screenshot.png --extract-text
    
    # Identify objects in image
    python image_understanding.py --image photo.jpg --identify-objects
    
    # Custom analysis with specific prompt
    python image_understanding.py --image photo.jpg --prompt "What brand is this product?"
    
    # Using API key from environment variable
    export OPENAI_API_KEY=your_key
    python image_understanding.py --image photo.jpg
    
    # Using remote image URL
    python image_understanding.py --image "https://example.com/photo.jpg" --describe

Environment Variables:
    OPENAI_API_KEY    Your OpenAI API key (can be used instead of --api-key)
        """
    )
    
    # Required arguments
    parser.add_argument(
        "--image",
        required=True,
        help="Path to local image file or URL of image"
    )
    
    # Optional arguments
    parser.add_argument(
        "--api-key",
        help="OpenAI API key (can also be set via OPENAI_API_KEY environment variable)"
    )
    
    parser.add_argument(
        "--model",
        default="gpt-4-vision-preview",
        help="Model to use for vision analysis (default: gpt-4-vision-preview)"
    )
    
    parser.add_argument(
        "--custom-prompt",
        "-p",
        help="Custom prompt for image analysis"
    )
    
    # Analysis type flags (mutually exclusive with custom prompt)
    analysis_group = parser.add_mutually_exclusive_group()
    analysis_group.add_argument(
        "--describe",
        action="store_true",
        default=True,
        help="Describe the image content (default behavior)"
    )
    analysis_group.add_argument(
        "--extract-text",
        action="store_true",
        help="Extract text from the image (OCR)"
    )
    analysis_group.add_argument(
        "--identify-objects",
        action="store_true",
        help="Identify and list objects in the image"
    )
    
    # Output options
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Output compact JSON (without indentation)"
    )
    
    args = parser.parse_args()
    
    try:
        result = execute(args)
        
        # Print result
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
