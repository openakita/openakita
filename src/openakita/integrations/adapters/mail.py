"""
邮件发送 API 适配器
支持 SendGrid 和阿里云邮件
"""

import aiohttp
import base64
from typing import Any, Dict, List, Optional
from . import BaseAPIAdapter, APIError, AuthenticationError


class SendGridAdapter(BaseAPIAdapter):
    """SendGrid 邮件服务适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化 SendGrid 适配器
        
        Args:
            config: 配置信息
                - api_key: SendGrid API Key
                - from_email: 发件人邮箱
                - from_name: 发件人名称（可选）
        """
        super().__init__(config)
        self.base_url = "https://api.sendgrid.com/v3"
        self.api_key = config.get('api_key')
        self.from_email = config.get('from_email')
        self.from_name = config.get('from_name', 'OpenAkita')
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def authenticate(self) -> bool:
        """验证 API Key 是否有效"""
        if not self.api_key:
            raise AuthenticationError("缺少 SendGrid API Key")
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                async with session.get(
                    f"{self.base_url}/scopes",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        return True
                    elif response.status == 401:
                        raise AuthenticationError("SendGrid API Key 无效")
                    else:
                        raise APIError(f"SendGrid 认证失败：{response.status}", status_code=response.status)
        except aiohttp.ClientError as e:
            raise APIError(f"SendGrid 连接失败：{str(e)}")
    
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
        """调用 SendGrid API"""
        if not self._session:
            self._session = aiohttp.ClientSession()
        
        self._log_request(endpoint, method, kwargs)
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                json=kwargs.get('json'),
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                result = await response.json()
                
                if response.status >= 400:
                    raise self._handle_error(response.status, result)
                
                return result
        except aiohttp.ClientError as e:
            raise APIError(f"SendGrid 调用失败：{str(e)}")
    
    async def send_email(
        self,
        to_emails: List[str],
        subject: str,
        content: str,
        content_type: str = "text/plain",
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        发送邮件
        
        Args:
            to_emails: 收件人邮箱列表
            subject: 邮件主题
            content: 邮件内容
            content_type: 内容类型 (text/plain 或 text/html)
            cc: 抄送邮箱列表
            bcc: 密送邮箱列表
            attachments: 附件列表
            
        Returns:
            API 响应
        """
        personalization = {
            "to": [{"email": email} for email in to_emails],
            "subject": subject
        }
        
        if cc:
            personalization["cc"] = [{"email": email} for email in cc]
        if bcc:
            personalization["bcc"] = [{"email": email} for email in bcc]
        
        payload = {
            "personalizations": [personalization],
            "from": {
                "email": self.from_email,
                "name": self.from_name
            },
            "content": [{
                "type": content_type,
                "value": content
            }]
        }
        
        if attachments:
            payload["attachments"] = attachments
        
        return await self.call("/mail/send", method="POST", json=payload)
    
    async def close(self):
        """关闭会话"""
        if self._session:
            await self._session.close()
            self._session = None


class AliyunMailAdapter(BaseAPIAdapter):
    """阿里云邮件推送适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化阿里云邮件适配器
        
        Args:
            config: 配置信息
                - access_key_id: AccessKey ID
                - access_key_secret: AccessKey Secret
                - account_name: 发件人地址
                - region: 区域 (默认 cn-hangzhou)
        """
        super().__init__(config)
        self.access_key_id = config.get('access_key_id')
        self.access_key_secret = config.get('access_key_secret')
        self.account_name = config.get('account_name')
        self.region = config.get('region', 'cn-hangzhou')
        self.endpoint = f"http://dm.{self.region}.aliyuncs.com"
        self._session: Optional[aiohttp.ClientSession] = None
    
    def _sign(self, params: Dict[str, str]) -> str:
        """生成阿里云签名"""
        import hmac
        import hashlib
        from urllib.parse import quote
        
        sorted_params = sorted(params.items())
        canonicalized = "&".join(f"{k}={quote(v, safe='')}" for k, v in sorted_params)
        
        string_to_sign = f"GET&%2F&{quote(canonicalized, safe='')}"
        
        signing_key = f"{self.access_key_secret}&"
        signature = hmac.new(
            signing_key.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1
        ).digest()
        
        return base64.b64encode(signature).decode('utf-8')
    
    async def authenticate(self) -> bool:
        """验证 AccessKey 是否有效"""
        if not self.access_key_id or not self.access_key_secret:
            raise AuthenticationError("缺少阿里云 AccessKey")
        
        try:
            params = {
                "Action": "GetAccountInfo",
                "Format": "JSON",
                "Version": "2015-11-23",
                "AccessKeyId": self.access_key_id,
                "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "SignatureNonce": str(uuid.uuid4()),
            }
            
            params["Signature"] = self._sign(params)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.endpoint,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    result = await response.json()
                    if response.status == 200 and "RequestId" in result:
                        return True
                    else:
                        raise APIError(f"阿里云邮件认证失败：{result}")
        except Exception as e:
            raise APIError(f"阿里云邮件认证失败：{str(e)}")
    
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
        """调用阿里云邮件 API"""
        if not self._session:
            self._session = aiohttp.ClientSession()
        
        params = kwargs.get('params', {})
        params.update({
            "Action": endpoint,
            "Format": "JSON",
            "Version": "2015-11-23",
            "AccessKeyId": self.access_key_id,
            "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
        })
        
        params["Signature"] = self._sign(params)
        
        try:
            async with self._session.request(
                method,
                self.endpoint,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                result = await response.json()
                
                if response.status >= 400:
                    raise self._handle_error(response.status, result)
                
                return result
        except aiohttp.ClientError as e:
            raise APIError(f"阿里云邮件调用失败：{str(e)}")
    
    async def send_email(
        self,
        to_address: str,
        subject: str,
        html_body: str,
        from_alias: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        发送邮件
        
        Args:
            to_address: 收件人邮箱
            subject: 邮件主题
            html_body: HTML 邮件内容
            from_alias: 发件人别名
            
        Returns:
            API 响应
        """
        params = {
            "AccountName": self.account_name,
            "AddressType": "1",  # 1=触发邮件
            "ToAddress": to_address,
            "Subject": subject,
            "HtmlBody": html_body,
            "ReplyToAddress": "false"
        }
        
        if from_alias:
            params["FromAlias"] = from_alias
        
        return await self.call("SingleSendMail", method="GET", params=params)
    
    async def close(self):
        """关闭会话"""
        if self._session:
            await self._session.close()
            self._session = None


# 工厂函数
def create_mail_adapter(provider: str, config: Dict[str, Any]) -> BaseAPIAdapter:
    """
    创建邮件 API 适配器
    
    Args:
        provider: 服务提供商 ('sendgrid' 或 'aliyun')
        config: 配置信息
        
    Returns:
        邮件适配器实例
    """
    providers = {
        'sendgrid': SendGridAdapter,
        'aliyun': AliyunMailAdapter
    }
    
    if provider not in providers:
        raise ValueError(f"不支持的邮件服务提供商：{provider}")
    
    return providers[provider](config)
