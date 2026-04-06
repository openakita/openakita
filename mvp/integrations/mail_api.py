"""
邮件发送 API - 阿里云邮件推送
"""

from typing import Dict, Any
import logging
from .base import BaseAPI, APIResponse, APIMode

logger = logging.getLogger(__name__)


class MailAPI(BaseAPI):
    """阿里云邮件推送 API"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        super().__init__(mode)
    
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock 模式：模拟邮件发送"""
        action = kwargs.get('action', 'send')
        
        if action == 'send':
            to_address = kwargs.get('to_address', '')
            subject = kwargs.get('subject', '')
            
            if not to_address or not subject:
                return APIResponse(
                    success=False,
                    data=None,
                    error="缺少必要参数：to_address, subject",
                    status_code=400
                )
            
            logger.info(f"[MOCK] 邮件发送成功 -> {to_address}, 主题：{subject}")
            return APIResponse(
                success=True,
                data={
                    'message_id': 'mock_msg_' + str(int(time.time())),
                    'to': to_address,
                    'subject': subject,
                    'status': 'sent'
                }
            )
        else:
            return APIResponse(
                success=False,
                data=None,
                error=f"未知操作：{action}",
                status_code=400
            )
    
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用"""
        try:
            import requests
            
            action = kwargs.get('action', 'send')
            
            if action == 'send':
                to_address = kwargs.get('to_address', '')
                subject = kwargs.get('subject', '')
                content = kwargs.get('content', '')
                
                # 阿里云邮件推送 API
                url = "https://dm.aliyuncs.com/"
                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded'
                }
                data = {
                    'Action': 'SingleSendMail',
                    'AccountName': self._config.get('ALIYUN_MAIL_ACCOUNT'),
                    'FromAlias': self._config.get('ALIYUN_MAIL_FROM'),
                    'ReceiverAddress': to_address,
                    'Subject': subject,
                    'HtmlBody': content,
                    'AccessKeyId': self._config.get('ALIYUN_ACCESS_KEY_ID'),
                    'AccessKeySecret': self._config.get('ALIYUN_ACCESS_KEY_SECRET')
                }
                
                response = requests.post(url, data=data, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    return APIResponse(
                        success=True,
                        data=response.json(),
                        status_code=200
                    )
                else:
                    return APIResponse(
                        success=False,
                        data=None,
                        error=f"API 返回错误：{response.text}",
                        status_code=response.status_code
                    )
            else:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"未知操作：{action}",
                    status_code=400
                )
                
        except Exception as e:
            return APIResponse(
                success=False,
                data=None,
                error=str(e),
                status_code=500
            )
    
    def send_mail(self, to_address: str, subject: str, content: str) -> APIResponse:
        """便捷方法：发送邮件"""
        return self.call(
            action='send',
            to_address=to_address,
            subject=subject,
            content=content
        )


# 测试用例
def test_mail_api():
    """邮件 API 测试"""
    import time
    
    print("=" * 50)
    print("邮件 API 测试")
    print("=" * 50)
    
    # 测试 1: Mock 模式发送成功
    print("\n[测试 1] Mock 模式 - 正常发送")
    api = MailAPI(mode=APIMode.MOCK)
    result = api.send_mail(
        to_address="test@example.com",
        subject="测试邮件",
        content="这是一封测试邮件"
    )
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    print(f"数据：{result.data}")
    
    # 测试 2: Mock 模式缺少参数
    print("\n[测试 2] Mock 模式 - 缺少参数")
    result = api.send_mail(
        to_address="",
        subject="测试邮件",
        content=""
    )
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    print(f"错误：{result.error}")
    
    # 测试 3: Mock 模式批量发送
    print("\n[测试 3] Mock 模式 - 批量发送")
    emails = [
        ("user1@example.com", "通知 1", "内容 1"),
        ("user2@example.com", "通知 2", "内容 2"),
        ("user3@example.com", "通知 3", "内容 3"),
    ]
    success_count = 0
    for to, subj, content in emails:
        result = api.send_mail(to, subj, content)
        if result.success:
            success_count += 1
    print(f"发送结果：{success_count}/{len(emails)} 成功")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_mail_api()
