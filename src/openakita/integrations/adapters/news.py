"""
新闻 API 适配器
支持聚合数据和天行数据
"""

from typing import Any

import aiohttp

from . import APIError, BaseAPIAdapter


class JuheNewsAdapter(BaseAPIAdapter):
    """聚合数据新闻 API 适配器"""

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
                    raise APIError(f"聚合数据 API 错误：{result.get('reason')}")
                return result

    async def get_top_news(self, page: int = 1, page_size: int = 10) -> dict:
        """获取头条新闻"""
        return await self.call("/toutiao/index", params={"page": page, "page_size": page_size})

    async def get_channel_news(
        self, channel: str = "top", page: int = 1, page_size: int = 10
    ) -> dict:
        """获取指定频道新闻"""
        return await self.call(
            "/toutiao/index", params={"channel": channel, "page": page, "page_size": page_size}
        )

    async def get_social_news(self, page: int = 1, page_size: int = 10) -> dict:
        """获取社会新闻"""
        return await self.get_channel_news("shehui", page, page_size)

    async def get_tech_news(self, page: int = 1, page_size: int = 10) -> dict:
        """获取科技新闻"""
        return await self.get_channel_news("keji", page, page_size)


class TianxingNewsAdapter(BaseAPIAdapter):
    """天行数据新闻 API 适配器"""

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
                    raise APIError(f"天行数据 API 错误：{result.get('msg')}")
                return result

    async def get_top_news(self, page: int = 1, num: int = 10) -> dict:
        """获取头条新闻"""
        return await self.call("/topworld/index", params={"page": page, "num": num})

    async def get_social_news(self, page: int = 1, num: int = 10) -> dict:
        """获取社会新闻"""
        return await self.call("/social/index", params={"page": page, "num": num})

    async def get_tech_news(self, page: int = 1, num: int = 10) -> dict:
        """获取科技新闻"""
        return await self.call("/tech/index", params={"page": page, "num": num})

    async def get_entertainment_news(self, page: int = 1, num: int = 10) -> dict:
        """获取娱乐新闻"""
        return await self.call("/huabian/index", params={"page": page, "num": num})

    async def get_sports_news(self, page: int = 1, num: int = 10) -> dict:
        """获取体育新闻"""
        return await self.call("/tiyu/index", params={"page": page, "num": num})


def create_news_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {"juhe": JuheNewsAdapter, "tianxing": TianxingNewsAdapter}
    if provider not in providers:
        raise ValueError(f"不支持的新闻提供商：{provider}")
    return providers[provider](config)
