#!/usr/bin/env python3
"""百度范模算法平台 - 百度千帆 AppBuilder 封装

算法实验管理、参数优化。

用法:
    python3 famou.py experiment "图像分类模型调优"
    python3 famou.py optimize "ResNet50 超参数搜索"
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_experiment_query(
    description: str, model_type: str = "", dataset: str = ""
) -> str:
    query = f"请创建一个算法实验：{description}"
    if model_type:
        query += f"，模型类型：{model_type}"
    if dataset:
        query += f"，使用数据集：{dataset}"
    query += "。请给出实验设计、基线配置、评估指标和预期目标。"
    return query


def build_optimize_query(
    task: str, search_space: str = "", method: str = ""
) -> str:
    query = f"请对以下任务进行参数优化：{task}"
    if search_space:
        query += f"，搜索空间：{search_space}"
    if method:
        query += f"，优化方法：{method}"
    query += "。请给出推荐的超参数组合、优化策略和预期效果提升。"
    return query


def main() -> None:
    parser = parse_common_args("百度范模 - 算法实验管理与参数优化")
    sub = parser.add_subparsers(dest="command", required=True)

    p_exp = sub.add_parser("experiment", help="创建算法实验")
    p_exp.add_argument("description", help="实验描述")
    p_exp.add_argument("--model-type", default="",
                       choices=["classification", "detection", "segmentation", "nlp", ""],
                       help="模型类型")
    p_exp.add_argument("--dataset", default="", help="数据集名称")

    p_opt = sub.add_parser("optimize", help="参数优化")
    p_opt.add_argument("task", help="优化任务描述")
    p_opt.add_argument("--search-space", default="", help="搜索空间定义（JSON 字符串）")
    p_opt.add_argument("--method", default="",
                       choices=["grid", "random", "bayesian", "hyperband", ""],
                       help="优化方法")

    args = parser.parse_args()

    if args.command == "experiment":
        query = build_experiment_query(args.description, args.model_type, args.dataset)
    else:
        query = build_optimize_query(args.task, args.search_space, args.method)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()

