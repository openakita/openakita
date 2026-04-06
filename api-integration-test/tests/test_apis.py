"""
API 集成测试用例
测试 10 个常用 API 的连通性和基本功能
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api_clients import get_client


def test_email_api():
    """测试邮件服务"""
    client = get_client("email")
    result = client.send_email(
        to="test@example.com",
        subject="API 集成测试",
        body="这是一封测试邮件"
    )
    print(f"✓ 邮件服务：{result['message']}")
    return result


def test_calendar_api():
    """测试日历服务"""
    client = get_client("calendar")
    result = client.create_event(
        summary="技术评审会",
        start_time="2026-03-20T14:00:00",
        end_time="2026-03-20T15:00:00"
    )
    print(f"✓ 日历服务：{result['message']}")
    return result


def test_sheets_api():
    """测试表格服务"""
    client = get_client("sheets")
    result = client.read_sheet()
    print(f"✓ 表格服务：{result['message']}")
    return result


def test_crm_api():
    """测试 CRM 服务"""
    client = get_client("crm")
    result = client.create_contact(
        email="customer@example.com",
        firstname="李",
        lastname="四"
    )
    print(f"✓ CRM 服务：{result['message']}")
    return result


def test_dingtalk_api():
    """测试钉钉消息"""
    client = get_client("dingtalk")
    result = client.send_message("Sprint 1 进度更新：API 集成验证完成 50%")
    print(f"✓ 钉钉消息：{result['message']}")
    return result


def test_oss_api():
    """测试云存储"""
    client = get_client("oss")
    result = client.upload_file("test.txt", "uploads/test.txt")
    print(f"✓ 云存储：{result['message']}")
    return result


def test_webhook_api():
    """测试 Webhook"""
    client = get_client("webhook")
    result = client.send_post(
        url="https://httpbin.org/post",
        data={"event": "test", "timestamp": "2026-03-13"}
    )
    print(f"✓ Webhook: {result['message']}")
    return result


def test_database_api():
    """测试数据库"""
    client = get_client("database")
    result = client.execute_query("SELECT * FROM users LIMIT 10")
    print(f"✓ 数据库：{result['message']}")
    return result


def test_pdf_api():
    """测试 PDF 生成"""
    client = get_client("pdf")
    result = client.generate_pdf(
        content="Sprint 1 进度报告",
        output_path="logs/report.pdf"
    )
    print(f"✓ PDF 生成：{result['message']}")
    return result


def test_sms_api():
    """测试短信服务"""
    client = get_client("sms")
    result = client.send_sms(
        phone_number="13800138000",
        template_params={"code": "123456"}
    )
    print(f"✓ 短信服务：{result['message']}")
    return result


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("API 集成验证测试 - Sprint 1")
    print("=" * 60)
    print()
    
    tests = [
        ("邮件服务", test_email_api),
        ("日历服务", test_calendar_api),
        ("表格服务", test_sheets_api),
        ("CRM 服务", test_crm_api),
        ("钉钉消息", test_dingtalk_api),
        ("云存储", test_oss_api),
        ("Webhook", test_webhook_api),
        ("数据库", test_database_api),
        ("PDF 生成", test_pdf_api),
        ("短信服务", test_sms_api),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append({"name": name, "status": "PASS", "result": result})
        except Exception as e:
            print(f"✗ {name}: {str(e)}")
            results.append({"name": name, "status": "FAIL", "error": str(e)})
    
    print()
    print("=" * 60)
    passed = sum(1 for r in results if r["status"] == "PASS")
    print(f"测试结果：{passed}/{len(results)} 通过")
    print("=" * 60)
    
    return results


if __name__ == "__main__":
    run_all_tests()
