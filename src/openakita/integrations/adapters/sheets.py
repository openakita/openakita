"""
表格处理 API 适配器
支持 Google Sheets 和腾讯文档
"""

from datetime import datetime
from typing import Any

import aiohttp

from . import APIError, AuthenticationError, BaseAPIAdapter


class GoogleSheetsAdapter(BaseAPIAdapter):
    """Google Sheets API 适配器"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化 Google Sheets 适配器

        Args:
            config: 配置信息
                - credentials: Google OAuth 凭据（service account JSON）
                - spreadsheet_id: 电子表格 ID
        """
        super().__init__(config)
        self.credentials = config.get('credentials')
        self.spreadsheet_id = config.get('spreadsheet_id')
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        self._session: aiohttp.ClientSession | None = None

    async def authenticate(self) -> bool:
        """获取访问令牌"""
        if not self.credentials:
            raise AuthenticationError("缺少 Google OAuth 凭据")

        # 检查现有令牌是否有效
        if self._token and self._token_expiry and datetime.utcnow() < self._token_expiry:
            return True

        try:
            # 使用 service account 获取访问令牌
            import jwt

            now = datetime.utcnow()
            payload = {
                'iss': self.credentials['client_email'],
                'scope': 'https://www.googleapis.com/auth/spreadsheets',
                'aud': 'https://oauth2.googleapis.com/token',
                'exp': int(now.timestamp()) + 3600,
                'iat': int(now.timestamp())
            }

            assertion = jwt.encode(
                payload,
                self.credentials['private_key'],
                algorithm='RS256'
            )

            async with aiohttp.ClientSession() as session, session.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                    'assertion': assertion
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()

                if response.status == 200:
                    self._token = result['access_token']
                    self._token_expiry = datetime.utcnow().replace(
                        second=datetime.utcnow().second + result['expires_in'] - 300
                    )
                    return True
                else:
                    raise AuthenticationError(f"Google OAuth 认证失败：{result}")
        except Exception as e:
            raise APIError(f"Google Sheets 认证失败：{str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """调用 Google Sheets API"""
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json"
        }

        try:
            async with self._session.request(
                method,
                f"https://sheets.googleapis.com/v4/{endpoint}",
                headers=headers,
                json=kwargs.get('json'),
                params=kwargs.get('params'),
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                result = await response.json()

                if response.status >= 400:
                    raise self._handle_error(response.status, result)

                return result
        except aiohttp.ClientError as e:
            raise APIError(f"Google Sheets 调用失败：{str(e)}")

    async def get_values(self, range: str) -> list[list[Any]]:
        """
        读取单元格数据

        Args:
            range: 范围（如 'Sheet1!A1:B10'）

        Returns:
            二维数组数据
        """
        result = await self.call(f"spreadsheets/{self.spreadsheet_id}/values/{range}")
        return result.get('values', [])

    async def update_values(self, range: str, values: list[list[Any]]) -> dict[str, Any]:
        """
        更新单元格数据

        Args:
            range: 范围（如 'Sheet1!A1:B10'）
            values: 二维数组数据

        Returns:
            API 响应
        """
        return await self.call(
            f"spreadsheets/{self.spreadsheet_id}/values/{range}",
            method="PUT",
            json={
                "values": values,
                "valueInputOption": "RAW"
            }
        )

    async def append_values(self, range: str, values: list[list[Any]]) -> dict[str, Any]:
        """
        追加数据

        Args:
            range: 范围
            values: 二维数组数据

        Returns:
            API 响应
        """
        return await self.call(
            f"spreadsheets/{self.spreadsheet_id}/values/{range}:append",
            method="POST",
            json={
                "values": values,
                "valueInputOption": "RAW",
                "insertDataOption": "INSERT_ROWS"
            }
        )

    async def close(self):
        """关闭会话"""
        if self._session:
            await self._session.close()
            self._session = None


class TencentDocsAdapter(BaseAPIAdapter):
    """腾讯文档 API 适配器"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化腾讯文档适配器

        Args:
            config: 配置信息
                - app_id: 应用 ID
                - secret_key: 密钥
                - spreadsheet_id: 表格 ID
        """
        super().__init__(config)
        self.app_id = config.get('app_id')
        self.secret_key = config.get('secret_key')
        self.spreadsheet_id = config.get('spreadsheet_id')
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def authenticate(self) -> bool:
        """获取访问令牌"""
        if not self.app_id or not self.secret_key:
            raise AuthenticationError("缺少腾讯文档认证信息")

        try:
            async with aiohttp.ClientSession() as session, session.post(
                'https://docs.qq.com/api/token',
                json={
                    'appId': self.app_id,
                    'secretKey': self.secret_key
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()

                if response.status == 200 and 'token' in result:
                    self._token = result['token']
                    return True
                else:
                    raise AuthenticationError(f"腾讯文档认证失败：{result}")
        except Exception as e:
            raise APIError(f"腾讯文档认证失败：{str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """调用腾讯文档 API"""
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json"
        }

        try:
            async with self._session.request(
                method,
                f"https://docs.qq.com/api/{endpoint}",
                headers=headers,
                json=kwargs.get('json'),
                params=kwargs.get('params'),
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                result = await response.json()

                if response.status >= 400:
                    raise self._handle_error(response.status, result)

                return result
        except aiohttp.ClientError as e:
            raise APIError(f"腾讯文档调用失败：{str(e)}")

    async def get_sheet_data(self, sheet_id: str) -> list[list[Any]]:
        """
        获取表格数据

        Args:
            sheet_id: 工作表 ID

        Returns:
            二维数组数据
        """
        result = await self.call(f"spreadsheet/{self.spreadsheet_id}/sheet/{sheet_id}/data")
        return result.get('data', [])

    async def update_cells(self, sheet_id: str, updates: list[dict]) -> dict[str, Any]:
        """
        更新单元格

        Args:
            sheet_id: 工作表 ID
            updates: 更新列表 [{'row': 1, 'col': 1, 'value': 'xxx'}]

        Returns:
            API 响应
        """
        return await self.call(
            f"spreadsheet/{self.spreadsheet_id}/sheet/{sheet_id}/cells",
            method="PUT",
            json={"updates": updates}
        )

    async def close(self):
        """关闭会话"""
        if self._session:
            await self._session.close()
            self._session = None


# 工厂函数
def create_sheets_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    """
    创建表格 API 适配器

    Args:
        provider: 服务提供商 ('google' 或 'tencent')
        config: 配置信息

    Returns:
        表格适配器实例
    """
    providers = {
        'google': GoogleSheetsAdapter,
        'tencent': TencentDocsAdapter
    }

    if provider not in providers:
        raise ValueError(f"不支持的表格服务提供商：{provider}")

    return providers[provider](config)
