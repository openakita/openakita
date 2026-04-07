"""
天气 API 适配器
支持和风天气和心知天气
"""

from typing import Any

import aiohttp

from . import APIError, BaseAPIAdapter


class QWeatherAdapter(BaseAPIAdapter):
    """和风天气 API 适配器"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.base_url = "https://devapi.qweather.com/v7"

    async def authenticate(self) -> bool:
        return bool(self.api_key)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        headers = kwargs.get('headers', {})
        headers['Authorization'] = f"Bearer {self.api_key}"

        async with aiohttp.ClientSession() as session:
            async with session.request(method, f"{self.base_url}{endpoint}", headers=headers) as response:
                result = await response.json()
                if result.get('code') != '200':
                    raise APIError(f"和风天气 API 错误：{result.get('msg')}")
                return result

    async def get_weather(self, location: str, type: str = 'now') -> dict:
        """获取天气信息"""
        return await self.call(f"/weather/{type}", params={'location': location})

    async def get_forecast(self, location: str, days: int = 3) -> dict:
        """获取天气预报"""
        return await self.call("/weather/3d", params={'location': location})

    async def get_indices(self, location: str, type: str = '1,2,3') -> dict:
        """获取生活指数"""
        return await self.call("/indices/1d", params={'location': location, 'type': type})

    async def get_city_info(self, location: str) -> dict:
        """获取城市信息"""
        return await self.call("/city/lookup", params={'location': location})


class HeartlyAdapter(BaseAPIAdapter):
    """心知天气 API 适配器"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.base_url = "https://api.seniverse.com/v3"

    async def authenticate(self) -> bool:
        return bool(self.api_key)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        params = kwargs.get('params', {})
        params['key'] = self.api_key

        async with aiohttp.ClientSession() as session:
            async with session.request(method, f"{self.base_url}{endpoint}", params=params) as response:
                result = await response.json()
                if 'status' in result and result['status'] != 'ok':
                    raise APIError(f"心知天气 API 错误：{result.get('status')}")
                return result

    async def get_weather(self, location: str) -> dict:
        """获取实时天气"""
        return await self.call("/weather/now.json", params={'location': location})

    async def get_forecast(self, location: str, days: int = 3) -> dict:
        """获取天气预报"""
        return await self.call("/weather/daily.json", params={'location': location, 'days': days})

    async def get_life_indices(self, location: str) -> dict:
        """获取生活指数"""
        return await self.call("/life/suggestion.json", params={'location': location})

    async def get_air_quality(self, location: str) -> dict:
        """获取空气质量"""
        return await self.call("/air/now.json", params={'location': location})


def create_weather_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {'qweather': QWeatherAdapter, 'heartly': HeartlyAdapter}
    if provider not in providers:
        raise ValueError(f"不支持的天气提供商：{provider}")
    return providers[provider](config)
