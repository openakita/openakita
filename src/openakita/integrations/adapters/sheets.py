"""
Spreadsheet API adapters
Supports Google Sheets and Tencent Docs
"""

from datetime import datetime
from typing import Any

import aiohttp

from . import APIError, AuthenticationError, BaseAPIAdapter


class GoogleSheetsAdapter(BaseAPIAdapter):
    """Google Sheets API adapter"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the Google Sheets adapter.

        Args:
            config: Configuration dict
                - credentials: Google OAuth credentials (service account JSON)
                - spreadsheet_id: Spreadsheet ID
        """
        super().__init__(config)
        self.credentials = config.get("credentials")
        self.spreadsheet_id = config.get("spreadsheet_id")
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        self._session: aiohttp.ClientSession | None = None

    async def authenticate(self) -> bool:
        """Obtain an access token."""
        if not self.credentials:
            raise AuthenticationError("Missing Google OAuth credentials")

        # Check if the existing token is still valid
        if self._token and self._token_expiry and datetime.utcnow() < self._token_expiry:
            return True

        try:
            # Use service account to obtain an access token
            import jwt

            now = datetime.utcnow()
            payload = {
                "iss": self.credentials["client_email"],
                "scope": "https://www.googleapis.com/auth/spreadsheets",
                "aud": "https://oauth2.googleapis.com/token",
                "exp": int(now.timestamp()) + 3600,
                "iat": int(now.timestamp()),
            }

            assertion = jwt.encode(payload, self.credentials["private_key"], algorithm="RS256")

            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response,
            ):
                result = await response.json()

                if response.status == 200:
                    self._token = result["access_token"]
                    self._token_expiry = datetime.utcnow().replace(
                        second=datetime.utcnow().second + result["expires_in"] - 300
                    )
                    return True
                else:
                    raise AuthenticationError(f"Google OAuth authentication failed: {result}")
        except Exception as e:
            raise APIError(f"Google Sheets authentication failed: {str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """Call the Google Sheets API."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self.authenticate()

        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

        try:
            async with self._session.request(
                method,
                f"https://sheets.googleapis.com/v4/{endpoint}",
                headers=headers,
                json=kwargs.get("json"),
                params=kwargs.get("params"),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                result = await response.json()

                if response.status >= 400:
                    raise self._handle_error(response.status, result)

                return result
        except aiohttp.ClientError as e:
            raise APIError(f"Google Sheets API call failed: {str(e)}")

    async def get_values(self, range: str) -> list[list[Any]]:
        """
        Read cell data.

        Args:
            range: Cell range (e.g. 'Sheet1!A1:B10')

        Returns:
            2D array of cell values
        """
        result = await self.call(f"spreadsheets/{self.spreadsheet_id}/values/{range}")
        return result.get("values", [])

    async def update_values(self, range: str, values: list[list[Any]]) -> dict[str, Any]:
        """
        Update cell data.

        Args:
            range: Cell range (e.g. 'Sheet1!A1:B10')
            values: 2D array of cell values

        Returns:
            API response
        """
        return await self.call(
            f"spreadsheets/{self.spreadsheet_id}/values/{range}",
            method="PUT",
            json={"values": values, "valueInputOption": "RAW"},
        )

    async def append_values(self, range: str, values: list[list[Any]]) -> dict[str, Any]:
        """
        Append data.

        Args:
            range: Cell range
            values: 2D array of cell values

        Returns:
            API response
        """
        return await self.call(
            f"spreadsheets/{self.spreadsheet_id}/values/{range}:append",
            method="POST",
            json={"values": values, "valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        )

    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None


class TencentDocsAdapter(BaseAPIAdapter):
    """Tencent Docs API adapter"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the Tencent Docs adapter.

        Args:
            config: Configuration dict
                - app_id: Application ID
                - secret_key: Secret key
                - spreadsheet_id: Spreadsheet ID
        """
        super().__init__(config)
        self.app_id = config.get("app_id")
        self.secret_key = config.get("secret_key")
        self.spreadsheet_id = config.get("spreadsheet_id")
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def authenticate(self) -> bool:
        """Obtain an access token."""
        if not self.app_id or not self.secret_key:
            raise AuthenticationError("Missing Tencent Docs credentials")

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    "https://docs.qq.com/api/token",
                    json={"appId": self.app_id, "secretKey": self.secret_key},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response,
            ):
                result = await response.json()

                if response.status == 200 and "token" in result:
                    self._token = result["token"]
                    return True
                else:
                    raise AuthenticationError(f"Tencent Docs authentication failed: {result}")
        except Exception as e:
            raise APIError(f"Tencent Docs authentication failed: {str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """Call the Tencent Docs API."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self.authenticate()

        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

        try:
            async with self._session.request(
                method,
                f"https://docs.qq.com/api/{endpoint}",
                headers=headers,
                json=kwargs.get("json"),
                params=kwargs.get("params"),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                result = await response.json()

                if response.status >= 400:
                    raise self._handle_error(response.status, result)

                return result
        except aiohttp.ClientError as e:
            raise APIError(f"Tencent Docs API call failed: {str(e)}")

    async def get_sheet_data(self, sheet_id: str) -> list[list[Any]]:
        """
        Get spreadsheet data.

        Args:
            sheet_id: Sheet ID

        Returns:
            2D array of cell values
        """
        result = await self.call(f"spreadsheet/{self.spreadsheet_id}/sheet/{sheet_id}/data")
        return result.get("data", [])

    async def update_cells(self, sheet_id: str, updates: list[dict]) -> dict[str, Any]:
        """
        Update cells.

        Args:
            sheet_id: Sheet ID
            updates: List of updates [{'row': 1, 'col': 1, 'value': 'xxx'}]

        Returns:
            API response
        """
        return await self.call(
            f"spreadsheet/{self.spreadsheet_id}/sheet/{sheet_id}/cells",
            method="PUT",
            json={"updates": updates},
        )

    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None


# Factory function
def create_sheets_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    """
    Create a spreadsheet API adapter.

    Args:
        provider: Service provider ('google' or 'tencent')
        config: Configuration dict

    Returns:
        Spreadsheet adapter instance
    """
    providers = {"google": GoogleSheetsAdapter, "tencent": TencentDocsAdapter}

    if provider not in providers:
        raise ValueError(f"Unsupported spreadsheet provider: {provider}")

    return providers[provider](config)
