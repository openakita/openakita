#!/usr/bin/env python3
"""百度深度研究 - 百度千帆 AppBuilder 封装

深度研究、报告生成。

用法:
    python3 deep_research.py research "人工智能在医疗领域的应用现状"
    python3 deep_research.py report "新能源汽车市场趋势"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_research_query(topic: str, depth: str = "comprehensive") -> str:
    depth_map = {
        "quick": "请做一个快速概览",
        "comprehensive": "请进行全面深入的研究分析",
        "expert": "请以专家级别深度进行研究，涵盖学术文献、行业报告和最新动态",
    }
    prefix = depth_map.get(depth, depth_map["comprehensive"])
    return f"{prefix}，主题：{topic}。请包含背景介绍、现状分析、关键发现和未来展望。"


def build_report_query(topic: str, format_type: str = "standard") -> str:
    query = f"请针对以下主题生成一份研究报告：{topic}"
    if format_type == "executive":
        query += "。请以管理层摘要格式呈现，突出关键结论和行动建议。"
    elif format_type == "academic":
        query += "。请以学术报告格式呈现，包含文献综述和研究方法论。"
    else:
        query += "。请包含摘要、研究背景、核心分析、数据支撑和结论建议。"
    return query


def main() -> None:
    parser = parse_common_args("百度深度研究 - 深度分析与报告生成")
    sub = parser.add_subparsers(dest="command", required=True)

    p_research = sub.add_parser("research", help="发起深度研究")
    p_research.add_argument("topic", help="研究主题")
    p_research.add_argument("--depth", default="comprehensive",
                            choices=["quick", "comprehensive", "expert"],
                            help="研究深度")

    p_report = sub.add_parser("report", help="生成研究报告")
    p_report.add_argument("topic", help="报告主题")
    p_report.add_argument("--format", dest="format_type", default="standard",
                          choices=["standard", "executive", "academic"],
                          help="报告格式")

    args = parser.parse_args()

    if args.command == "research":
        query = build_research_query(args.topic, args.depth)
    else:
        query = build_report_query(args.topic, args.format_type)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()
