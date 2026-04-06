"""
MVP API 集成验证测试脚本
验证 10 个常用 API 集成方案的完整性和可用性
"""

import os
import sys
import importlib.util
from pathlib import Path
from typing import Dict, List, Tuple


class APIIntegrationValidator:
    """API 集成验证器"""
    
    def __init__(self, api_dir: str):
        self.api_dir = Path(api_dir)
        self.validation_results = {}
    
    def load_module(self, filepath: Path) -> object:
        """动态加载 Python 模块"""
        spec = importlib.util.spec_from_file_location(filepath.stem, filepath)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            return module
        except Exception as e:
            print(f"✗ 加载模块失败 {filepath.name}: {e}")
            return None
    
    def check_class_structure(self, module: object, expected_classes: List[str]) -> Dict:
        """检查模块中的类结构"""
        result = {
            'exists': [],
            'missing': [],
            'methods': {}
        }
        
        for class_name in expected_classes:
            if hasattr(module, class_name):
                cls = getattr(module, class_name)
                result['exists'].append(class_name)
                
                # 检查公共方法
                methods = [m for m in dir(cls) if not m.startswith('_') and callable(getattr(cls, m))]
                result['methods'][class_name] = methods
            else:
                result['missing'].append(class_name)
        
        return result
    
    def validate_api_file(self, filename: str) -> Dict:
        """验证单个 API 文件"""
        filepath = self.api_dir / filename
        
        if not filepath.exists():
            return {
                'file': filename,
                'status': 'MISSING',
                'error': '文件不存在'
            }
        
        # 加载模块
        module = self.load_module(filepath)
        if not module:
            return {
                'file': filename,
                'status': 'LOAD_FAILED',
                'error': '模块加载失败'
            }
        
        # 定义每个 API 文件期望的类
        expected_classes_map = {
            '01_email_api.py': ['EmailAPI', 'SendGridAPI'],
            '02_webhook_api.py': ['WebhookClient', 'WebhookServer'],
            '03_database_api.py': ['PostgreSQLAPI', 'MySQLAPI'],
            '04_storage_api.py': ['LocalStorageAPI', 'S3StorageAPI', 'OSSStorageAPI'],
            '05_message_api.py': ['DingTalkAPI', 'WeComAPI', 'FeishuAPI'],
            '06_calendar_api.py': ['GoogleCalendarAPI'],
            '07_document_api.py': ['GoogleDocsAPI', 'TencentDocsAPI'],
            '08_spreadsheet_api.py': ['GoogleSheetsAPI', 'ExcelAPI'],
            '09_auth_api.py': ['JWTAuthAPI', 'OAuth2API', 'PasswordHashAPI'],
            '10_logging_api.py': ['StructuredLogger', 'PerformanceMonitor', 'SentryErrorTracker', 'PrometheusMetrics']
        }
        
        expected_classes = expected_classes_map.get(filename, [])
        structure = self.check_class_structure(module, expected_classes)
        
        # 验证结果
        if structure['missing']:
            status = 'PARTIAL'
            error = f"缺少类：{', '.join(structure['missing'])}"
        else:
            status = 'VALID'
            error = None
        
        return {
            'file': filename,
            'status': status,
            'error': error,
            'classes': structure['exists'],
            'methods': structure['methods']
        }
    
    def validate_all(self) -> Dict:
        """验证所有 API 文件"""
        api_files = [
            '01_email_api.py',
            '02_webhook_api.py',
            '03_database_api.py',
            '04_storage_api.py',
            '05_message_api.py',
            '06_calendar_api.py',
            '07_document_api.py',
            '08_spreadsheet_api.py',
            '09_auth_api.py',
            '10_logging_api.py'
        ]
        
        print("=" * 80)
        print("MVP API 集成验证测试")
        print("=" * 80)
        print()
        
        results = {}
        valid_count = 0
        partial_count = 0
        failed_count = 0
        
        for filename in api_files:
            print(f"验证 {filename}...", end=" ")
            result = self.validate_api_file(filename)
            results[filename] = result
            
            if result['status'] == 'VALID':
                print("✅ 通过")
                valid_count += 1
            elif result['status'] == 'PARTIAL':
                print(f"⚠️  部分通过 - {result['error']}")
                partial_count += 1
            else:
                print(f"❌ 失败 - {result['error']}")
                failed_count += 1
        
        print()
        print("=" * 80)
        print(f"验证汇总：✅ {valid_count} 通过 | ⚠️  {partial_count} 部分 | ❌ {failed_count} 失败")
        print("=" * 80)
        
        return {
            'total': len(api_files),
            'valid': valid_count,
            'partial': partial_count,
            'failed': failed_count,
            'details': results
        }
    
    def generate_report(self, output_file: str = None):
        """生成验证报告"""
        results = self.validate_all()
        
        report_lines = [
            "# MVP API 集成验证报告",
            "",
            "**验证时间**: 2026-03-14",
            "**验证工具**: API Integration Validator",
            "",
            "## 验证汇总",
            "",
            f"- **总文件数**: {results['total']}",
            f"- **✅ 通过**: {results['valid']}",
            f"- **⚠️  部分通过**: {results['partial']}",
            f"- **❌ 失败**: {results['failed']}",
            "",
            f"**通过率**: {results['valid']/results['total']*100:.1f}%",
            "",
            "## 详细验证结果",
            ""
        ]
        
        for filename, result in results['details'].items():
            status_emoji = {
                'VALID': '✅',
                'PARTIAL': '⚠️',
                'FAILED': '❌',
                'MISSING': '❌',
                'LOAD_FAILED': '❌'
            }.get(result['status'], '❓')
            
            report_lines.append(f"### {status_emoji} {filename}")
            report_lines.append("")
            report_lines.append(f"- **状态**: {result['status']}")
            
            if result.get('error'):
                report_lines.append(f"- **问题**: {result['error']}")
            
            if result.get('classes'):
                report_lines.append(f"- **验证类**: {', '.join(result['classes'])}")
            
            if result.get('methods'):
                report_lines.append("")
                report_lines.append("**主要方法**:")
                for class_name, methods in result['methods'].items():
                    report_lines.append(f"  - `{class_name}`: {', '.join(methods[:5])}{'...' if len(methods) > 5 else ''}")
            
            report_lines.append("")
        
        report = "\n".join(report_lines)
        
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"\n📄 验证报告已保存：{output_file}")
        
        return report


def main():
    """主函数"""
    api_dir = Path(__file__).parent
    
    validator = APIIntegrationValidator(api_dir)
    
    # 执行验证
    results = validator.validate_all()
    
    # 生成报告
    report = validator.generate_report(output_file=api_dir / "VALIDATION_REPORT.md")
    
    # 返回退出码
    if results['failed'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
