"""
Email sending API adapter
Supports SendGrid and Alibaba Cloud DirectMail
"""

import base64
from typing import Any

import aiohttp

from . import APIError, AuthenticationError, BaseAPIAdapter


class SendGridAdapter(BaseAPIAdapter):
    """SendGrid email service adapter"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the SendGrid adapter.

        Args:
            config: configuration
                - api_key: SendGrid API Key
                - from_email: sender email address
                - from_name: sender display name (optional)
        """
        super().__init__(config)
        self.base_url = "https://api.sendgrid.com/v3"
        self.api_key = config.get("api_key")
        self.from_email = config.get("from_email")
        self.from_name = config.get("from_name", "OpenAkita")
        self._session: aiohttp.ClientSession | None = None

    async def authenticate(self) -> bool:
        """Verify that the API Key is valid."""
        if not self.api_key:
            raise AuthenticationError("Missing SendGrid API Key")

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                async with session.get(
                    f"{self.base_url}/scopes",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        return True
                    elif response.status == 401:
                        raise AuthenticationError("Invalid SendGrid API Key")
                    else:
                        raise APIError(
                            f"SendGrid authentication failed: {response.status}", status_code=response.status
                        )
        except aiohttp.ClientError as e:
            raise APIError(f"SendGrid connection failed: {str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """Call the SendGrid API."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        self._log_request(endpoint, method, kwargs)

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            async with self._session.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                json=kwargs.get("json"),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                result = await response.json()

                if response.status >= 400:
                    raise self._handle_error(response.status, result)

                return result
        except aiohttp.ClientError as e:
            raise APIError(f"SendGrid call failed: {str(e)}")

    async def send_email(
        self,
        to_emails: list[str],
        subject: str,
        content: str,
        content_type: str = "text/plain",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Send an email.

        Args:
            to_emails: list of recipient email addresses
            subject: email subject
            content: email body
            content_type: content type (text/plain or text/html)
            cc: list of CC email addresses
            bcc: list of BCC email addresses
            attachments: list of attachments

        Returns:
            API response
        """
        personalization = {"to": [{"email": email} for email in to_emails], "subject": subject}

        if cc:
            personalization["cc"] = [{"email": email} for email in cc]
        if bcc:
            personalization["bcc"] = [{"email": email} for email in bcc]

        payload = {
            "personalizations": [personalization],
            "from": {"email": self.from_email, "name": self.from_name},
            "content": [{"type": content_type, "value": content}],
        }

        if attachments:
            payload["attachments"] = attachments

        return await self.call("/mail/send", method="POST", json=payload)

    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None


class AliyunMailAdapter(BaseAPIAdapter):
    """Alibaba Cloud DirectMail adapter."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the Alibaba Cloud DirectMail adapter.

        Args:
            config: configuration
                - access_key_id: AccessKey ID
                - access_key_secret: AccessKey Secret
                - account_name: sender email address
                - region: region (default cn-hangzhou)
        """
        super().__init__(config)
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.account_name = config.get("account_name")
        self.region = config.get("region", "cn-hangzhou")
        self.endpoint = f"http://dm.{self.region}.aliyuncs.com"
        self._session: aiohttp.ClientSession | None = None

    def _sign(self, params: dict[str, str]) -> str:
        """Generate the Alibaba Cloud signature."""
        import hashlib
        import hmac
        from urllib.parse import quote

        sorted_params = sorted(params.items())
        canonicalized = "&".join(f"{k}={quote(v, safe='')}" for k, v in sorted_params)

        string_to_sign = f"GET&%2F&{quote(canonicalized, safe='')}"

        signing_key = f"{self.access_key_secret}&"
        signature = hmac.new(
            signing_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1
        ).digest()

        return base64.b64encode(signature).decode("utf-8")

    async def authenticate(self) -> bool:
        """Verify that the AccessKey is valid."""
        if not self.access_key_id or not self.access_key_secret:
            raise AuthenticationError("Missing Alibaba Cloud AccessKey")

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

            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    self.endpoint, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as response,
            ):
                result = await response.json()
                if response.status == 200 and "RequestId" in result:
                    return True
                else:
                    raise APIError(f"Alibaba Cloud DirectMail authentication failed: {result}")
        except Exception as e:
            raise APIError(f"Alibaba Cloud DirectMail authentication failed: {str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """Call the Alibaba Cloud DirectMail API."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        params = kwargs.get("params", {})
        params.update(
            {
                "Action": endpoint,
                "Format": "JSON",
                "Version": "2015-11-23",
                "AccessKeyId": self.access_key_id,
                "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "SignatureNonce": str(uuid.uuid4()),
            }
        )

        params["Signature"] = self._sign(params)

        try:
            async with self._session.request(
                method, self.endpoint, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                result = await response.json()

                if response.status >= 400:
                    raise self._handle_error(response.status, result)

                return result
        except aiohttp.ClientError as e:
            raise APIError(f"Alibaba Cloud DirectMail call failed: {str(e)}")

    async def send_email(
        self, to_address: str, subject: str, html_body: str, from_alias: str | None = None
    ) -> dict[str, Any]:
        """
        Send an email.

        Args:
            to_address: recipient email address
            subject: email subject
            html_body: HTML email body
            from_alias: sender alias

        Returns:
            API response
        """
        params = {
            "AccountName": self.account_name,
            "AddressType": "1",  # 1=trigger email
            "ToAddress": to_address,
            "Subject": subject,
            "HtmlBody": html_body,
            "ReplyToAddress": "false",
        }

        if from_alias:
            params["FromAlias"] = from_alias

        return await self.call("SingleSendMail", method="GET", params=params)

    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None


# Factory function
def create_mail_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    """
    Create an email API adapter.

    Args:
        provider: service provider ('sendgrid' or 'aliyun')
        config: configuration

    Returns:
        email adapter instance
    """
    providers = {"sendgrid": SendGridAdapter, "aliyun": AliyunMailAdapter}

    if provider not in providers:
        raise ValueError(f"Unsupported email service provider: {provider}")

    return providers[provider](config)
