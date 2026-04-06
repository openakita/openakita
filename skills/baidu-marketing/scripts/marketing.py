#!/usr/bin/env python3
"""百度营销助手 - 百度千帆 AppBuilder 封装

营销文案生成、营销方案策划。

用法:
    python3 marketing.py copywrite "新品咖啡上市推广"
    python3 marketing.py plan "双十一电商营销策略"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_copywrite_query(theme: str, style: str = "", platform: str = "") -> str:
    query = f"请为以下主题生成营销文案：{theme}"
    if style:
        query += f"，文案风格：{style}"
    if platform:
        query += f"，投放平台：{platform}"
    query += "。请提供标题、正文、号召性用语（CTA）和配套的标签/话题建议。"
    return query


def build_plan_query(objective: str, budget: str = "", duration: str = "") -> str:
    query = f"请制定一份营销方案，目标：{objective}"
    if budget:
        query += f"，预算：{budget}"
    if duration:
        query += f"，周期：{duration}"
    query += "。请包含目标受众分析、渠道策略、内容规划、时间表和预期效果评估。"
    return query


def main() -> None:
    parser = parse_common_args("百度营销助手 - 文案生成与方案策划")
    sub = parser.add_subparsers(dest="command", required=True)

    p_copy = sub.add_parser("copywrite", help="生成营销文案")
    p_copy.add_argument("theme", help="文案主题")
    p_copy.add_argument("--style", default="",
                        choices=["formal", "casual", "humorous", "emotional", ""],
                        help="文案风格")
    p_copy.add_argument("--platform", default="",
                        choices=["wechat", "weibo", "douyin", "xiaohongshu", ""],
                        help="投放平台")

    p_plan = sub.add_parser("plan", help="制定营销方案")
    p_plan.add_argument("objective", help="营销目标")
    p_plan.add_argument("--budget", default="", help="预算")
    p_plan.add_argument("--duration", default="", help="活动周期")

    args = parser.parse_args()

    if args.command == "copywrite":
        query = build_copywrite_query(args.theme, args.style, args.platform)
    else:
        query = build_plan_query(args.objective, args.budget, args.duration)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()
