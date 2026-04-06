def send_email(to, subject, body, cc=None):
    """
    模拟发送邮件的函数
    
    Args:
        to: 收件人邮箱
        subject: 邮件主题
        body: 邮件正文
        cc: 抄送人邮箱（可选，默认 None）
    
    Returns:
        bool: 始终返回 True 表示发送成功
    """
    print(f"正在发送邮件...")
    print(f"收件人：{to}")
    print(f"主题：{subject}")
    print(f"正文：{body}")
    print(f"抄送：{cc}")
    print(f"邮件发送成功！")
    return True


if __name__ == "__main__":
    # 测试示例
    result = send_email("test@example.com", "测试邮件", "这是一封测试邮件的正文内容")
    print(f"函数返回值：{result}")
