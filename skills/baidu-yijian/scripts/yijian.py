#!/usr/bin/env python3
"""百度一见工业视觉 - 百度千帆 AppBuilder 封装

工业视觉检测、缺陷分析。

用法:
    python3 yijian.py detect --image /path/to/product.jpg
    python3 yijian.py report --image /path/to/product.jpg
"""

import base64
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def read_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def build_detect_query(image_path: str, detect_type: str = "") -> str:
    query = "请对以下工业产品图片进行视觉缺陷检测"
    if detect_type:
        query += f"，检测类型：{detect_type}"
    if os.path.isfile(image_path):
        b64 = read_image_base64(image_path)
        query += f"\n[图片已编码，base64 长度: {len(b64)}]\n图片数据：{b64[:200]}..."
    else:
        query += f"\n图片路径：{image_path}"
    query += "\n请识别缺陷类型、位置、严重程度，并给出判定结果。"
    return query


def build_report_query(image_path: str) -> str:
    query = "请对以下工业检测图片生成详细的质检分析报告"
    if os.path.isfile(image_path):
        b64 = read_image_base64(image_path)
        query += f"\n[图片已编码，base64 长度: {len(b64)}]\n图片数据：{b64[:200]}..."
    else:
        query += f"\n图片路径：{image_path}"
    query += "\n请包含检测概况、缺陷明细、统计数据、质量评级和改进建议。"
    return query


def main() -> None:
    parser = parse_common_args("百度一见 - 工业视觉检测")
    sub = parser.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect", help="缺陷检测")
    p_detect.add_argument("--image", required=True, help="产品图片路径")
    p_detect.add_argument("--type", dest="detect_type", default="",
                          choices=["surface", "dimension", "assembly", ""],
                          help="检测类型")

    p_report = sub.add_parser("report", help="生成质检分析报告")
    p_report.add_argument("--image", required=True, help="产品图片路径")

    args = parser.parse_args()

    if args.command == "detect":
        query = build_detect_query(args.image, args.detect_type)
    else:
        query = build_report_query(args.image)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()
