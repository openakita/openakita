#!/usr/bin/env python3
"""
SMTP 连接测试 - 验证 SMTP 配置是否正确
"""

import os
import sys
import smtplib
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)

# SMTP 配置
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
SMTP_FROM_EMAIL = os.getenv('SMTP_FROM_EMAIL', SMTP_USERNAME)


def test_smtp_connection():
    """测试 SMTP 连接"""
    
    print("=" * 60)
    print("  SMTP 连接测试")
    print("=" * 60)
    print()
    
    # 检查配置
    if not SMTP_USERNAME:
        print("❌ 错误：未配置 SMTP_USERNAME")
        print("   请先运行配置脚本：python scripts/configure_smtp.py")
        return False
    
    if not SMTP_PASSWORD:
        print("❌ 错误：未配置 SMTP_PASSWORD")
        print("   请先运行配置脚本：python scripts/configure_smtp.py")
        return False
    
    print("配置信息:")
    print(f"  SMTP 服务器：{SMTP_SERVER}:{SMTP_PORT}")
    print(f"  邮箱账号：{SMTP_USERNAME}")
    print(f"  TLS: {SMTP_USE_TLS}")
    print()
    
    try:
        print("正在连接 SMTP 服务器...")
        
        # 连接服务器
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            print("  ✓ 已建立 TLS 连接")
        else:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
            print("  ✓ 已建立 SSL 连接")
        
        # 登录
        print("正在登录...")
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        print("  ✓ 登录成功")
        
        # 发送测试邮件给自己
        print("正在发送测试邮件...")
        
        test_subject = "SMTP 配置测试"
        test_body = """
你好！

这是一封测试邮件，用于验证 SMTP 配置是否正确。

如果收到了这封邮件，说明你的 SMTP 配置已成功！

祝好，
SMTP Email Sender
        """
        
        from email.mime.text import MIMEText
        msg = MIMEText(test_body, 'plain', 'utf-8')
        msg['From'] = SMTP_FROM_EMAIL
        msg['To'] = SMTP_USERNAME
        msg['Subject'] = test_subject
        
        server.sendmail(SMTP_FROM_EMAIL, [SMTP_USERNAME], msg.as_string())
        print("  ✓ 测试邮件已发送")
        
        server.quit()
        
        print()
        print("=" * 60)
        print("  ✅ SMTP 配置测试成功！")
        print("=" * 60)
        print()
        print("你现在可以使用 SMTP 邮件发送功能了。")
        print()
        print("使用示例:")
        print('  python scripts/send_email.py \\')
        print('    --to recipient@example.com \\')
        print('    --subject "邮件主题" \\')
        print('    --body "邮件正文"')
        print()
        
        return True
        
    except smtplib.SMTPAuthenticationError:
        print()
        print("=" * 60)
        print("  ❌ SMTP 认证失败！")
        print("=" * 60)
        print()
        print("可能原因:")
        print("  1. 密码错误")
        print("  2. Gmail 用户未使用应用专用密码")
        print("  3. 账号开启了安全保护")
        print()
        print("解决方案:")
        print("  Gmail 用户请访问：https://myaccount.google.com/apppasswords")
        print("  创建应用专用密码并使用该密码")
        return False
        
    except smtplib.SMTPConnectError as e:
        print()
        print("=" * 60)
        print("  ❌ 无法连接到 SMTP 服务器！")
        print("=" * 60)
        print()
        print(f"错误信息：{str(e)}")
        print()
        print("可能原因:")
        print("  1. SMTP 服务器地址错误")
        print("  2. 端口错误")
        print("  3. 网络连接问题")
        print("  4. 防火墙阻止")
        print()
        print("请检查配置并重试")
        return False
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"  ❌ 测试失败：{str(e)}")
        print("=" * 60)
        return False


if __name__ == '__main__':
    success = test_smtp_connection()
    sys.exit(0 if success else 1)
