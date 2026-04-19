"""
SMS API adapter
Supports Alibaba Cloud SMS and Tencent Cloud SMS
"""

import hashlib
import hmac
from datetime import datetime
from typing import Any

import aiohttp

from . import APIError, BaseAPIAdapter


class AliyunSMSAdapter(BaseAPIAdapter):
    """Alibaba Cloud SMS adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.sign_name = config.get("sign_name")
        self.endpoint = "http://dysmsapi.aliyuncs.com"

    def _sign(self, params: dict) -> str:
        sorted_params = sorted(params.items())
        canonicalized = "&".join(f"{k}={v}" for k, v in sorted_params)
        string_to_sign = f"GET&%2F&{canonicalized}"
        signature = hmac.new(
            f"{self.access_key_secret}&".encode(), string_to_sign.encode(), hashlib.sha1
        ).digest()
        return base64.b64encode(signature).decode()

    async def authenticate(self) -> bool:
        return bool(self.access_key_id and self.access_key_secret)

    async def send_sms(self, phone_numbers: str, template_code: str, template_param: dict) -> dict:
        import base64
        import uuid

        params = {
            "Action": "SendSms",
            "Format": "JSON",
            "Version": "2017-05-25",
            "AccessKeyId": self.access_key_id,
            "PhoneNumbers": phone_numbers,
            "SignName": self.sign_name,
            "TemplateCode": template_code,
            "TemplateParam": base64.b64encode(json.dumps(template_param).encode()).decode(),
            "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
        }
        params["Signature"] = self._sign(params)
        async with aiohttp.ClientSession() as session:
            async with session.get(self.endpoint, params=params) as response:
                result = await response.json()
                if result.get("Code") != "OK":
                    raise APIError(f"Send failed: {result.get('Message')}")
                return result


class TencentSMSAdapter(BaseAPIAdapter):
    """Tencent Cloud SMS adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.secret_id = config.get("secret_id")
        self.secret_key = config.get("secret_key")
        self.app_id = config.get("app_id")
        self.sign_name = config.get("sign_name")
        self.endpoint = "https://sms.tencentcloudapi.com"

    async def authenticate(self) -> bool:
        return bool(self.secret_id and self.secret_key)

    async def send_sms(
        self, phone_numbers: list[str], template_id: str, template_param: list[str]
    ) -> dict:
        import base64
        import hashlib
        import hmac
        import json
        import time

        timestamp = int(time.time())
        payload = {
            "PhoneNumberSet": phone_numbers,
            "TemplateID": template_id,
            "TemplateParamSet": template_param,
            "SignName": self.sign_name,
            "SmsSdkAppId": self.app_id,
        }
        body = json.dumps(payload)
        hashed_request_payload = hashlib.sha256(body.encode()).hexdigest()
        string_to_sign = f"POST\nsms.tencentcloudapi.com\n/\n\n\n\n\n{hashed_request_payload}"
        signature = hmac.new(
            self.secret_key.encode(), string_to_sign.encode(), hashlib.sha256
        ).digest()
        signature = base64.b64encode(signature).decode()
        headers = {
            "Authorization": f"TC3-HMAC-SHA256 Credential={self.secret_id}/2023-01-01/sms/tc3_request, SignedHeaders=content-type;host, Signature={signature}",
            "Content-Type": "application/json",
            "Host": "sms.tencentcloudapi.com",
            "X-TC-Action": "SendSms",
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": "2021-01-11",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.endpoint, headers=headers, data=body) as response:
                result = await response.json()
                if response.status >= 400:
                    raise APIError(f"Send failed: {result}")
                return result


def create_sms_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {"aliyun": AliyunSMSAdapter, "tencent": TencentSMSAdapter}
    if provider not in providers:
        raise ValueError(f"Unsupported SMS provider: {provider}")
    return providers[provider](config)
