"""
Weather API adapters
Supports QWeather and Seniverse (Heartly) weather services
"""

from typing import Any

import aiohttp

from . import APIError, BaseAPIAdapter


class QWeatherAdapter(BaseAPIAdapter):
    """QWeather API adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key")
        self.base_url = "https://devapi.qweather.com/v7"

    async def authenticate(self) -> bool:
        return bool(self.api_key)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        headers = kwargs.get("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, f"{self.base_url}{endpoint}", headers=headers
            ) as response:
                result = await response.json()
                if result.get("code") != "200":
                    raise APIError(f"QWeather API error: {result.get('msg')}")
                return result

    async def get_weather(self, location: str, type: str = "now") -> dict:
        """Get weather information"""
        return await self.call(f"/weather/{type}", params={"location": location})

    async def get_forecast(self, location: str, days: int = 3) -> dict:
        """Get weather forecast"""
        return await self.call("/weather/3d", params={"location": location})

    async def get_indices(self, location: str, type: str = "1,2,3") -> dict:
        """Get living indices"""
        return await self.call("/indices/1d", params={"location": location, "type": type})

    async def get_city_info(self, location: str) -> dict:
        """Get city information"""
        return await self.call("/city/lookup", params={"location": location})


class HeartlyAdapter(BaseAPIAdapter):
    """Seniverse (Heartly) Weather API adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key")
        self.base_url = "https://api.seniverse.com/v3"

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
                if "status" in result and result["status"] != "ok":
                    raise APIError(f"Seniverse Weather API error: {result.get('status')}")
                return result

    async def get_weather(self, location: str) -> dict:
        """Get current weather"""
        return await self.call("/weather/now.json", params={"location": location})

    async def get_forecast(self, location: str, days: int = 3) -> dict:
        """Get weather forecast"""
        return await self.call("/weather/daily.json", params={"location": location, "days": days})

    async def get_life_indices(self, location: str) -> dict:
        """Get living indices"""
        return await self.call("/life/suggestion.json", params={"location": location})

    async def get_air_quality(self, location: str) -> dict:
        """Get air quality"""
        return await self.call("/air/now.json", params={"location": location})


def create_weather_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {"qweather": QWeatherAdapter, "heartly": HeartlyAdapter}
    if provider not in providers:
        raise ValueError(f"Unsupported weather provider: {provider}")
    return providers[provider](config)
