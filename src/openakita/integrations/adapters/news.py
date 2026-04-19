"""
News API adapter.
Supports Juhe Data and Tianxing Data providers.
"""

from typing import Any

import aiohttp

from . import APIError, BaseAPIAdapter


class JuheNewsAdapter(BaseAPIAdapter):
    """Juhe Data News API adapter."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key")
        self.base_url = "http://v.juhe.cn"

    async def authenticate(self) -> bool:
        return bool(self.api_key)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        params = kwargs.get("params", {})
        params["key"] = self.api_key

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, f"{self.base_url}{endpoint}", params=params
            ) as response:
                result = await response.json()
                if result.get("error_code") != 0:
                    raise APIError(f"Juhe Data API error: {result.get('reason')}")
                return result

    async def get_top_news(self, page: int = 1, page_size: int = 10) -> dict:
        """Get top headline news."""
        return await self.call("/toutiao/index", params={"page": page, "page_size": page_size})

    async def get_channel_news(
        self, channel: str = "top", page: int = 1, page_size: int = 10
    ) -> dict:
        """Get news for a specific channel."""
        return await self.call(
            "/toutiao/index", params={"channel": channel, "page": page, "page_size": page_size}
        )

    async def get_social_news(self, page: int = 1, page_size: int = 10) -> dict:
        """Get social news."""
        return await self.get_channel_news("shehui", page, page_size)

    async def get_tech_news(self, page: int = 1, page_size: int = 10) -> dict:
        """Get tech news."""
        return await self.get_channel_news("keji", page, page_size)


class TianxingNewsAdapter(BaseAPIAdapter):
    """Tianxing Data News API adapter."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key")
        self.base_url = "https://api.tianapi.com"

    async def authenticate(self) -> bool:
        return bool(self.api_key)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        params = kwargs.get("params", {})
        params["key"] = self.api_key

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, f"{self.base_url}{endpoint}", params=params
            ) as response:
                result = await response.json()
                if result.get("code") != 200:
                    raise APIError(f"Tianxing Data API error: {result.get('msg')}")
                return result

    async def get_top_news(self, page: int = 1, num: int = 10) -> dict:
        """Get top headline news."""
        return await self.call("/topworld/index", params={"page": page, "num": num})

    async def get_social_news(self, page: int = 1, num: int = 10) -> dict:
        """Get social news."""
        return await self.call("/social/index", params={"page": page, "num": num})

    async def get_tech_news(self, page: int = 1, num: int = 10) -> dict:
        """Get tech news."""
        return await self.call("/tech/index", params={"page": page, "num": num})

    async def get_entertainment_news(self, page: int = 1, num: int = 10) -> dict:
        """Get entertainment news."""
        return await self.call("/huabian/index", params={"page": page, "num": num})

    async def get_sports_news(self, page: int = 1, num: int = 10) -> dict:
        """Get sports news."""
        return await self.call("/tiyu/index", params={"page": page, "num": num})


def create_news_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {"juhe": JuheNewsAdapter, "tianxing": TianxingNewsAdapter}
    if provider not in providers:
        raise ValueError(f"Unsupported news provider: {provider}")
    return providers[provider](config)
