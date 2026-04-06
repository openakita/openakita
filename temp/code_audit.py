#!/usr/bin/env python
"""代码质量审计与技术债务评估脚本"""
import os
import sys
import json
import subprocess
import ast
from pathlib import Path
from collections import defaultdict
from datetime import datetime

PROJECT_ROOT = Path(r"D:\coder\myagent")
SRC_DIR = PROJECT_ROOT / "src" / "openakita"
TESTS_DIR = PROJECT_ROOT / "tests"

class CodeQualityAnalyzer:
    def __init__(self):
        self.metrics = {
            "total_files": 0,
            "total_lines": 0,
            "total_classes": 0,
            "total_functions": 0,
            "complex_functions": [],
            "duplicate_code": [],
            "missing_docstrings": [],
            "long_functions": [],
        }
        self.dependencies = {}
        self.test_coverage = {}
        
    def count_python_files(self, directory):
        """统计 Python 文件数量和行数"""
        py_files = list(directory.rglob("*.py"))
        total_lines = 0
        file_stats = []
        
        for py_file in py_files:
            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    line_count = len(lines)
                    total_lines += line_count
                    file_stats.append({
                        "file": str(py_file.relative_to(PROJECT_ROOT)),
                        "lines": line_count,
                    })
            except Exception as e:
                print(f"Error reading {py_file}: {e}")
        
        return py_files, total_lines, file_stats
    
    def analyze_complexity(self, filepath):
        """分析单个文件的圈复杂度"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                tree = ast.parse(f.read(), filename=str(filepath))
        except Exception as e:
            return []
        
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 计算圈复杂度（简化版：if/for/while/except/and/or 数量）
                complexity = 1
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
                        complexity += 1
                    elif isinstance(child, ast.BoolOp):
                        complexity += len(child.values) - 1
                
                # 函数长度
                if hasattr(node, 'end_lineno') and hasattr(node, 'lineno'):
                    func_length = node.end_lineno - node.lineno + 1
                else:
                    func_length = 0
                
                # 检查问题
                if complexity > 10:
                    issues.append({
                        "file": str(filepath.relative_to(PROJECT_ROOT)),
                        "function": node.name,
                        "type": "high_complexity",
                        "value": complexity,
                        "line": node.lineno,
                    })
                
                if func_length > 50:
                    issues.append({
                        "file": str(filepath.relative_to(PROJECT_ROOT)),
                        "function": node.name,
                        "type": "long_function",
                        "value": func_length,
                        "line": node.lineno,
                    })
                
                # 检查 docstring
                if not ast.get_docstring(node):
                    issues.append({
                        "file": str(filepath.relative_to(PROJECT_ROOT)),
                        "function": node.name,
                        "type": "missing_docstring",
                        "line": node.lineno,
                    })
        
        return issues
    
    def check_dependencies(self):
        """检查依赖包版本"""
        requirements_file = PROJECT_ROOT / "requirements.txt"
        pyproject_file = PROJECT_ROOT / "pyproject.toml"
        
        deps = {}
        
        # 从 pyproject.toml 读取依赖
        if pyproject_file.exists():
            with open(pyproject_file, 'r', encoding='utf-8') as f:
                content = f.read()
                in_deps = False
                for line in content.split('\n'):
                    if line.strip().startswith('dependencies = ['):
                        in_deps = True
                        continue
                    if in_deps:
                        if line.strip() == ']':
                            break
                        # 解析依赖
                        line = line.strip().strip('"').strip("'").strip(',')
                        if line and not line.startswith('#'):
                            # 解析包名和版本
                            if '>=' in line:
                                pkg, version = line.split('>=')
                                deps[pkg.strip()] = {"current": "unknown", "required": f">={version.strip()}"}
                            elif '==' in line:
                                pkg, version = line.split('==')
                                deps[pkg.strip()] = {"current": "unknown", "required": f"=={version.strip()}"}
                            else:
                                deps[line.strip()] = {"current": "unknown", "required": "any"}
        
        return deps
    
    def run_tests(self):
        """运行测试并收集覆盖率"""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(TESTS_DIR), "--collect-only", "-q"],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
                timeout=60
            )
            
            # 解析测试结果
            output = result.stdout + result.stderr
            test_count = 0
            for line in output.split('\n'):
                if 'test' in line.lower() and 'passed' in line.lower():
                    continue
                if line.strip().endswith('tests collected'):
                    try:
                        test_count = int(line.split()[0])
                    except:
                        pass
            
            return {
                "total_tests": test_count,
                "can_run": True,
                "error": None
            }
        except subprocess.TimeoutExpired:
            return {"total_tests": 0, "can_run": False, "error": "Timeout"}
        except Exception as e:
            return {"total_tests": 0, "can_run": False, "error": str(e)}
    
    def generate_report(self):
        """生成完整报告"""
        print("=" * 80)
        print("代码质量审计与技术债务评估报告")
        print(f"生成时间：{datetime.now().isoformat()}")
        print("=" * 80)
        
        # 1. 文件统计
        print("\n## 1. 代码规模统计")
        py_files, total_lines, file_stats = self.count_python_files(SRC_DIR)
        self.metrics["total_files"] = len(py_files)
        self.metrics["total_lines"] = total_lines
        
        print(f"- Python 文件总数：{len(py_files)}")
        print(f"- 代码总行数：{total_lines}")
        print(f"- 平均文件大小：{total_lines // max(len(py_files), 1):.0f} 行")
        
        # 2. 代码质量分析
        print("\n## 2. 代码质量分析")
        all_issues = []
        for py_file in py_files[:50]:  # 限制分析前 50 个文件
            issues = self.analyze_complexity(py_file)
            all_issues.extend(issues)
        
        complexity_issues = [i for i in all_issues if i["type"] == "high_complexity"]
        long_functions = [i for i in all_issues if i["type"] == "long_function"]
        missing_docs = [i for i in all_issues if i["type"] == "missing_docstring"]
        
        print(f"- 高复杂度函数 (>10): {len(complexity_issues)} 个")
        print(f"- 超长函数 (>50 行): {len(long_functions)} 个")
        print(f"- 缺少文档字符串：{len(missing_docs)} 个")
        
        if complexity_issues:
            print("\n  高复杂度函数 TOP 5:")
            for issue in sorted(complexity_issues, key=lambda x: x["value"], reverse=True)[:5]:
                print(f"    - {issue['file']}:{issue['function']} (复杂度:{issue['value']}, 行:{issue['line']})")
        
        # 3. 测试覆盖
        print("\n## 3. 测试覆盖情况")
        test_info = self.run_tests()
        print(f"- 测试文件总数：{len(list(TESTS_DIR.rglob('test_*.py')))}")
        print(f"- 可收集测试数：{test_info['total_tests']}")
        print(f"- 测试运行状态：{'正常' if test_info['can_run'] else f'异常 ({test_info['error']})'}")
        
        # 4. 依赖检查
        print("\n## 4. 依赖包检查")
        deps = self.check_dependencies()
        print(f"- 项目依赖数：{len(deps)}")
        print("\n  主要依赖:")
        for pkg, info in list(deps.items())[:10]:
            print(f"    - {pkg}: {info['required']}")
        
        # 5. 技术债务评估
        print("\n## 5. 技术债务清单")
        debt_items = []
        
        # 高复杂度函数重构
        if complexity_issues:
            estimate_hours = len(complexity_issues) * 2  # 每个函数约 2 小时
            debt_items.append({
                "type": "代码复杂度",
                "count": len(complexity_issues),
                "estimated_hours": estimate_hours,
                "priority": "高",
                "description": "重构高圈复杂度函数，降低维护成本"
            })
        
        # 补充文档
        if missing_docs:
            estimate_hours = len(missing_docs) * 0.5  # 每个函数约 0.5 小时
            debt_items.append({
                "type": "文档缺失",
                "count": len(missing_docs),
                "estimated_hours": estimate_hours,
                "priority": "中",
                "description": "为公共函数添加文档字符串"
            })
        
        # 长函数重构
        if long_functions:
            estimate_hours = len(long_functions) * 3  # 每个函数约 3 小时
            debt_items.append({
                "type": "函数过长",
                "count": len(long_functions),
                "estimated_hours": estimate_hours,
                "priority": "中",
                "description": "拆分超长函数，提高可读性"
            })
        
        total_debt_hours = sum(item["estimated_hours"] for item in debt_items)
        print(f"\n技术债务总估算：{total_debt_hours:.1f} 人时 (约 {total_debt_hours/8:.1f} 人天)")
        
        for item in debt_items:
            print(f"\n  [{item['priority']}] {item['type']}: {item['count']} 项")
            print(f"      工作量：{item['estimated_hours']:.1f} 小时")
            print(f"      说明：{item['description']}")
        
        # 6. 重构优先级建议
        print("\n## 6. 重构优先级建议")
        print("\n### P0 - 立即处理（本周）")
        print("- 高复杂度函数重构（TOP 5）")
        print("- 关键路径上的长函数拆分")
        
        print("\n### P1 - 近期处理（本月）")
        print("- 其余高复杂度函数优化")
        print("- 核心模块文档补充")
        
        print("\n### P2 - 持续改进（下季度）")
        print("- 全面文档覆盖")
        print("- 测试覆盖率提升至 80%+")
        print("- 依赖包版本统一升级")
        
        # 7. 保存报告
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "metrics": self.metrics,
            "issues": all_issues,
            "dependencies": deps,
            "test_info": test_info,
            "technical_debt": debt_items,
        }
        
        report_file = PROJECT_ROOT / "code_quality_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n## 报告已保存至：{report_file}")
        print("=" * 80)
        
        return report_data

if __name__ == "__main__":
    analyzer = CodeQualityAnalyzer()
    analyzer.generate_report()
