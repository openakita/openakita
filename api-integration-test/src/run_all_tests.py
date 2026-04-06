#!/usr/bin/env python3
"""
API 集成验证测试执行入口
Sprint 1 技术评审会批准事项
"""

import sys
import os
from datetime import datetime

# 添加项目根目录到路径
project_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, project_root)

# 导入测试模块
from tests.test_apis import run_all_tests


def generate_report(results):
    """生成测试报告"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    report = f"""# API 集成验证报告

**生成时间**: {timestamp}
**项目**: Sprint 1 - 10 个常用 API 集成验证
**状态**: {'✅ 全部通过' if all(r['status'] == 'PASS' for r in results) else '⚠️ 部分失败'}

## 测试结果汇总

| API 名称 | 状态 | 说明 |
|----------|------|------|
"""
    
    for r in results:
        status_icon = "✅" if r["status"] == "PASS" else "❌"
        message = r.get("result", {}).get("message", r.get("error", "未知错误"))
        report += f"| {r['name']} | {status_icon} {r['status']} | {message} |\n"
    
    passed = sum(1 for r in results if r["status"] == "PASS")
    report += f"""
## 统计

- **总计**: {len(results)} 个 API
- **通过**: {passed} 个
- **失败**: {len(results) - passed} 个
- **通过率**: {passed/len(results)*100:.1f}%

## 下一步

1. 配置实际 API 密钥（config/.env）
2. 运行真实环境测试
3. 集成到工作流编排器

---
*报告由 API 集成验证项目自动生成*
"""
    
    return report


def main():
    print("\n" + "=" * 60)
    print("Sprint 1 - API 集成验证")
    print("开始时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60 + "\n")
    
    # 运行测试
    results = run_all_tests()
    
    # 生成报告
    report = generate_report(results)
    
    # 保存报告
    logs_dir = os.path.join(project_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    report_path = os.path.join(logs_dir, "test_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"\n📄 测试报告已保存：{report_path}")
    print("\n" + "=" * 60)
    
    # 返回退出码
    if all(r["status"] == "PASS" for r in results):
        print("✅ 所有 API 验证通过")
        return 0
    else:
        print("⚠️ 部分 API 验证失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
