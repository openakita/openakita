"""
Cloud storage API adapters
Supports Alibaba Cloud OSS and Qiniu Cloud
"""

import base64
import hashlib
import hmac
from datetime import datetime
from typing import Any

import aiohttp

from . import BaseAPIAdapter


class AliyunOSSAdapter(BaseAPIAdapter):
    """Alibaba Cloud OSS adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.bucket = config.get("bucket")
        self.endpoint = config.get("endpoint", "oss-cn-hangzhou.aliyuncs.com")
        self._session = None

    def _sign(self, method: str, resource: str, headers: dict) -> str:
        date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        headers["Date"] = date
        sign_str = f"{method}\n\n\n{date}\n{resource}"
        signature = base64.b64encode(
            hmac.new(self.access_key_secret.encode(), sign_str.encode(), hashlib.sha1).digest()
        ).decode()
        return f"OSS {self.access_key_id}:{signature}"

    async def authenticate(self) -> bool:
        return bool(self.access_key_id and self.access_key_secret)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        if not self._session:
            self._session = aiohttp.ClientSession()

        resource = f"/{self.bucket}/{endpoint}"
        headers = kwargs.get("headers", {})
        headers["Host"] = f"{self.bucket}.{self.endpoint}"
        headers["Authorization"] = self._sign(method, resource, headers)

        url = f"https://{self.bucket}.{self.endpoint}/{endpoint}"
        async with self._session.request(method, url, headers=headers, **kwargs) as response:
            if response.status >= 400:
                raise self._handle_error(response.status, await response.text())
            return {"status": response.status}

    async def upload(
        self, object_key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> dict:
        headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
        return await self.call(object_key, "PUT", data=data, headers=headers)

    async def download(self, object_key: str) -> bytes:
        if not self._session:
            self._session = aiohttp.ClientSession()
        resource = f"/{self.bucket}/{object_key}"
        headers = {"Host": f"{self.bucket}.{self.endpoint}"}
        headers["Authorization"] = self._sign("GET", resource, headers)
        url = f"https://{self.bucket}.{self.endpoint}/{object_key}"
        async with self._session.get(url, headers=headers) as response:
            if response.status >= 400:
                raise self._handle_error(response.status, await response.text())
            return await response.read()

    async def close(self):
        if self._session:
            await self._session.close()


class QiniuAdapter(BaseAPIAdapter):
    """Qiniu Cloud storage adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.access_key = config.get("access_key")
        self.secret_key = config.get("secret_key")
        self.bucket = config.get("bucket")
        self._session = None

    async def authenticate(self) -> bool:
        return bool(self.access_key and self.secret_key)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        raise NotImplementedError("Qiniu uploads require form-based upload; please use the upload method")

    async def upload(self, key: str, data: bytes, token: str | None = None) -> dict:
        if not self._session:
            self._session = aiohttp.ClientSession()

        if not token:
            token = self._generate_upload_token()

        form = aiohttp.FormData()
        form.add_field("token", token)
        form.add_field("key", key)
        form.add_field("file", data, filename="file")

        async with self._session.post("https://up.qiniup.com", data=form) as response:
            result = await response.json()
            if response.status >= 400:
                raise self._handle_error(response.status, result)
            return result

    def _generate_upload_token(self) -> str:
        import json
        import time

        policy = {"scope": self.bucket, "deadline": int(time.time()) + 3600}
        encoded_policy = base64.urlsafe_b64encode(json.dumps(policy).encode()).decode()
        signature = hmac.new(
            self.secret_key.encode(), encoded_policy.encode(), hashlib.sha1
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).decode()
        return f"{self.access_key}:{encoded_signature}:{encoded_policy}"

    async def close(self):
        if self._session:
            await self._session.close()


def create_storage_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {"aliyun": AliyunOSSAdapter, "qiniu": QiniuAdapter}
    if provider not in providers:
        raise ValueError(f"Unsupported storage provider: {provider}")
    return providers[provider](config)
