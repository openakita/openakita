#!/usr/bin/env python3
"""
SMTP 配置向导 - 帮助用户配置 SMTP 邮箱设置
"""

import os
import sys
from pathlib import Path
from dotenv import set_key

# .env 文件路径
env_path = Path(__file__).parent.parent.parent / '.env'


def print_welcome():
    print("=" * 60)
    print("  SMTP 邮件发送配置向导")
    print("=" * 60)
    print()


def print_email_providers():
    print("常用邮箱 SMTP 配置：")
    print()
    print("📧 Gmail:")
    print("   SMTP 服务器：smtp.gmail.com")
    print("   端口：587 (TLS) 或 465 (SSL)")
    print("   注意：需要启用两步验证并创建应用专用密码")
    print("   应用密码：https://myaccount.google.com/apppasswords")
    print()
    print("📧 Outlook/Hotmail:")
    print("   SMTP 服务器：smtp-mail.outlook.com")
    print("   端口：587 (TLS)")
    print()
    print("📧 QQ 邮箱:")
    print("   SMTP 服务器：smtp.qq.com")
    print("   端口：587 (TLS)")
    print("   注意：需要在设置中开启 SMTP 服务")
    print()
    print("📧 163 邮箱:")
    print("   SMTP 服务器：smtp.163.com")
    print("   端口：587 (TLS)")
    print()
    print("📧 企业邮箱:")
    print("   请联系 IT 部门获取 SMTP 配置")
    print()


def get_user_input():
    print("请选择你的邮箱服务商：")
    print("1. Gmail")
    print("2. Outlook/Hotmail")
    print("3. QQ 邮箱")
    print("4. 163 邮箱")
    print("5. 其他（手动输入配置）")
    print()
    
    choice = input("请输入选项 (1-5): ").strip()
    
    if choice == '1':
        return {
            'server': 'smtp.gmail.com',
            'port': '587',
            'tls': 'true'
        }
    elif choice == '2':
        return {
            'server': 'smtp-mail.outlook.com',
            'port': '587',
            'tls': 'true'
        }
    elif choice == '3':
        return {
            'server': 'smtp.qq.com',
            'port': '587',
            'tls': 'true'
        }
    elif choice == '4':
        return {
            'server': 'smtp.163.com',
            'port': '587',
            'tls': 'true'
        }
    else:
        print()
        server = input("SMTP 服务器地址：").strip()
        port = input("SMTP 端口 (默认 587): ").strip() or '587'
        tls = input("是否使用 TLS? (true/false, 默认 true): ").strip() or 'true'
        return {
            'server': server,
            'port': port,
            'tls': tls
        }


def save_config(smtp_config):
    print()
    print("-" * 60)
    print("请输入你的邮箱账号信息：")
    print()
    
    email = input("邮箱地址：").strip()
    password = input("密码（或应用专用密码）: ").strip()
    from_name = input("发件人显示名称（可选，直接回车跳过）: ").strip()
    
    if not email or not password:
        print("❌ 邮箱和密码不能为空！")
        return False
    
    # 保存到 .env 文件
    print()
    print("正在保存配置...")
    
    set_key(str(env_path), 'SMTP_SERVER', smtp_config['server'])
    set_key(str(env_path), 'SMTP_PORT', smtp_config['port'])
    set_key(str(env_path), 'SMTP_USERNAME', email)
    set_key(str(env_path), 'SMTP_PASSWORD', password)
    set_key(str(env_path), 'SMTP_USE_TLS', smtp_config['tls'])
    set_key(str(env_path), 'SMTP_FROM_EMAIL', email)
    
    if from_name:
        set_key(str(env_path), 'SMTP_FROM_NAME', from_name)
    
    print("✅ 配置已保存到 .env 文件")
    print()
    print("-" * 60)
    print("配置摘要：")
    print(f"  SMTP 服务器：{smtp_config['server']}:{smtp_config['port']}")
    print(f"  邮箱账号：{email}")
    print(f"  TLS: {smtp_config['tls']}")
    if from_name:
        print(f"  发件人名称：{from_name}")
    print()
    
    return True


def test_connection():
    """测试 SMTP 连接"""
    print("是否要测试 SMTP 连接？(y/n): ", end='')
    test = input().strip().lower()
    
    if test != 'y':
        return
    
    print()
    print("正在测试连接...")
    
    # 加载配置
    load_dotenv()
    
    import smtplib
    
    server = os.getenv('SMTP_SERVER')
    port = int(os.getenv('SMTP_PORT', '587'))
    username = os.getenv('SMTP_USERNAME')
    password = os.getenv('SMTP_PASSWORD')
    use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
    
    try:
        if use_tls:
            smtp = smtplib.SMTP(server, port)
            smtp.starttls()
        else:
            smtp = smtplib.SMTP_SSL(server, port)
        
        smtp.login(username, password)
        smtp.quit()
        
        print("✅ SMTP 连接测试成功！")
        print()
        print("🎉 配置完成！你现在可以使用 SMTP 邮件发送功能了。")
        print()
        print("使用示例:")
        print('  python skills/smtp-email-sender/scripts/send_email.py \\')
        print('    --to friend@example.com \\')
        print('    --subject "你好" \\')
        print('    --body "这是一封测试邮件"')
        
    except Exception as e:
        print(f"❌ 连接测试失败：{str(e)}")
        print()
        print("可能的原因:")
        print("  1. 密码错误（Gmail 用户请使用应用专用密码）")
        print("  2. 网络连接问题")
        print("  3. SMTP 服务器地址或端口错误")
        print("  4. 防火墙阻止")


def main():
    print_welcome()
    print_email_providers()
    
    smtp_config = get_user_input()
    
    if save_config(smtp_config):
        test_connection()
    else:
        sys.exit(1)


if __name__ == '__main__':
    # 需要导入 load_dotenv
    from dotenv import load_dotenv
    main()
