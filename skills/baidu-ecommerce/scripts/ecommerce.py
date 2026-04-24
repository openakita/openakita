#!/usr/bin/env python3
"""百度电商助手 - 百度千帆 AppBuilder 封装

商品比价、口碑分析、选购建议。

用法:
    python3 ecommerce.py compare "iPhone 16 Pro"
    python3 ecommerce.py review "戴森吸尘器 V15"
    python3 ecommerce.py recommend "2000元价位降噪耳机"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_compare_query(product: str, platforms: str = "") -> str:
    query = f"请对商品「{product}」进行多平台比价分析"
    if platforms:
        query += f"，重点关注以下平台：{platforms}"
    query += "。请列出各平台价格、优惠活动和购买建议。"
    return query


def build_review_query(product: str) -> str:
    return (
        f"请分析商品「{product}」的用户口碑，"
        "汇总正面评价、负面评价、常见问题，并给出综合评分。"
    )


def build_recommend_query(requirement: str, budget: str = "") -> str:
    query = f"请根据以下需求推荐商品：{requirement}"
    if budget:
        query += f"，预算范围：{budget}"
    query += "。请给出3-5个推荐选项，附带优缺点对比。"
    return query


def main() -> None:
    parser = parse_common_args("百度电商助手 - 比价、口碑与选购建议")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compare = sub.add_parser("compare", help="多平台比价")
    p_compare.add_argument("product", help="商品名称或型号")
    p_compare.add_argument("--platforms", default="", help="指定平台，逗号分隔")

    p_review = sub.add_parser("review", help="口碑分析")
    p_review.add_argument("product", help="商品名称")

    p_recommend = sub.add_parser("recommend", help="选购推荐")
    p_recommend.add_argument("requirement", help="需求描述")
    p_recommend.add_argument("--budget", default="", help="预算范围")

    args = parser.parse_args()

    if args.command == "compare":
        query = build_compare_query(args.product, args.platforms)
    elif args.command == "review":
        query = build_review_query(args.product)
    else:
        query = build_recommend_query(args.requirement, args.budget)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()

