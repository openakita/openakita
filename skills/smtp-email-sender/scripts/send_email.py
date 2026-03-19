#!/usr/bin/env python3
"""
SMTP Email Sender - 通过 SMTP 发送邮件

用法:
    python scripts/send_email.py --to recipient@example.com --subject "主题" --body "正文"
    
支持:
    - 多个收件人、抄送、密送
    - HTML 格式邮件
    - 附件
    - Gmail/Outlook/QQ/163 等邮箱
"""

import argparse
import smtplib
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
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
SMTP_FROM_NAME = os.getenv('SMTP_FROM_NAME', '')


def send_email(to: str, subject: str, body: str, 
               cc: str = None, bcc: str = None,
               attachments: str = None, is_html: bool = False,
               from_name: str = None) -> dict:
    """
    发送邮件
    
    参数:
        to: 收件人邮箱（多个用逗号分隔）
        subject: 邮件主题
        body: 邮件正文
        cc: 抄送邮箱（多个用逗号分隔）
        bcc: 密送邮箱（多个用逗号分隔）
        attachments: 附件路径（多个用逗号分隔）
        is_html: 是否为 HTML 格式
        from_name: 发件人显示名称
        
    返回:
        dict: {'success': bool, 'message': str}
    """
    
    # 验证配置
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        return {
            'success': False,
            'message': '缺少 SMTP 配置！请在 .env 文件中设置 SMTP_USERNAME 和 SMTP_PASSWORD，或运行配置脚本。'
        }
    
    # 解析收件人列表
    to_list = [email.strip() for email in to.split(',')]
    cc_list = [email.strip() for email in cc.split(',')] if cc else []
    bcc_list = [email.strip() for email in bcc.split(',')] if bcc else []
    
    # 所有收件人
    all_recipients = to_list + cc_list + bcc_list
    
    # 创建邮件
    msg = MIMEMultipart()
    msg['From'] = f"{from_name or SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>" if from_name or SMTP_FROM_NAME else SMTP_FROM_EMAIL
    msg['To'] = ', '.join(to_list)
    msg['Subject'] = subject
    
    if cc_list:
        msg['Cc'] = ', '.join(cc_list)
    
    # 添加正文
    content_type = 'html' if is_html else 'plain'
    msg.attach(MIMEText(body, content_type, 'utf-8'))
    
    # 添加附件
    if attachments:
        attachment_paths = [path.strip() for path in attachments.split(',')]
        for file_path in attachment_paths:
            try:
                with open(file_path, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    
                    # 设置附件文件名
                    filename = os.path.basename(file_path)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{filename}"'
                    )
                    msg.attach(part)
            except Exception as e:
                return {
                    'success': False,
                    'message': f'附件 {file_path} 添加失败：{str(e)}'
                }
    
    # 发送邮件
    try:
        # 连接服务器
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        
        # 登录
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        
        # 发送
        server.sendmail(SMTP_FROM_EMAIL, all_recipients, msg.as_string())
        server.quit()
        
        return {
            'success': True,
            'message': f'邮件已成功发送至：{", ".join(to_list)}' + 
                      (f' (抄送：{", ".join(cc_list)})' if cc_list else '') +
                      (f' (密送：{len(bcc_list)} 人)' if bcc_list else '')
        }
        
    except smtplib.SMTPAuthenticationError:
        return {
            'success': False,
            'message': 'SMTP 认证失败！请检查用户名和密码（Gmail 用户请使用应用专用密码）'
        }
    except smtplib.SMTPConnectError:
        return {
            'success': False,
            'message': f'无法连接到 SMTP 服务器 {SMTP_SERVER}:{SMTP_PORT}，请检查网络和配置'
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'发送失败：{str(e)}'
        }


def main():
    parser = argparse.ArgumentParser(description='通过 SMTP 发送邮件')
    
    parser.add_argument('--to', required=True, help='收件人邮箱（多个用逗号分隔）')
    parser.add_argument('--subject', required=True, help='邮件主题')
    parser.add_argument('--body', required=True, help='邮件正文')
    parser.add_argument('--cc', help='抄送邮箱（多个用逗号分隔）')
    parser.add_argument('--bcc', help='密送邮箱（多个用逗号分隔）')
    parser.add_argument('--attachment', help='附件路径（多个用逗号分隔）')
    parser.add_argument('--is_html', action='store_true', help='是否为 HTML 格式')
    parser.add_argument('--from_name', help='发件人显示名称')
    
    args = parser.parse_args()
    
    result = send_email(
        to=args.to,
        subject=args.subject,
        body=args.body,
        cc=args.cc,
        bcc=args.bcc,
        attachments=args.attachment,
        is_html=args.is_html,
        from_name=args.from_name
    )
    
    if result['success']:
        print(f"✅ {result['message']}")
        sys.exit(0)
    else:
        print(f"❌ {result['message']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
