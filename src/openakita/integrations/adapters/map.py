"""
Map API adapters
Supports Amap (Gaode) and Baidu Maps
"""

from typing import Any

import aiohttp

from . import APIError, BaseAPIAdapter


class AMapAdapter(BaseAPIAdapter):
    """Amap (Gaode) Map API adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key")
        self.base_url = "https://restapi.amap.com/v3"

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
                if result.get("status") != "1":
                    raise APIError(f"Amap API error: {result.get('info')}")
                return result

    async def geocode(self, address: str, city: str | None = None) -> dict:
        """Geocoding: address to coordinates"""
        params = {"address": address}
        if city:
            params["city"] = city
        return await self.call("/geocode/geo", params=params)

    async def regeocode(self, location: str) -> dict:
        """Reverse geocoding: coordinates to address"""
        return await self.call("/geocode/regeo", params={"location": location})

    async def weather(self, city: str, extensions: str = "base") -> dict:
        """Get weather information"""
        return await self.call(
            "/weather/weatherInfo", params={"city": city, "extensions": extensions}
        )

    async def direction_driving(self, origin: str, destination: str) -> dict:
        """Route planning: driving"""
        return await self.call(
            "/direction/driving", params={"origin": origin, "destination": destination}
        )

    async def place_search(
        self, keywords: str, city: str | None = None, types: str | None = None
    ) -> dict:
        """POI search"""
        params = {"keywords": keywords}
        if city:
            params["city"] = city
        if types:
            params["types"] = types
        return await self.call("/place/text", params=params)


class BaiduMapAdapter(BaseAPIAdapter):
    """Baidu Map API adapter"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key")
        self.base_url = "https://api.map.baidu.com"

    async def authenticate(self) -> bool:
        return bool(self.api_key)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        params = kwargs.get("params", {})
        params["ak"] = self.api_key
        params["output"] = "json"

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, f"{self.base_url}{endpoint}", params=params
            ) as response:
                result = await response.json()
                if result.get("status") != 0:
                    raise APIError(f"Baidu Map API error: {result.get('message')}")
                return result

    async def geocode(self, address: str, city: str | None = None) -> dict:
        """Geocoding"""
        params = {"address": address}
        if city:
            params["city"] = city
        return await self.call("/geocoding/v3/", params=params)

    async def regeocode(self, location: str) -> dict:
        """Reverse geocoding"""
        return await self.call("/reverse_geocoding/v3/", params={"location": location})

    async def weather(self, location: str) -> dict:
        """Weather query"""
        return await self.call("/weather/v1", params={"location": location})

    async def direction_driving(self, origin: str, destination: str) -> dict:
        """Driving route planning"""
        return await self.call(
            "/direction/v2/driving", params={"origin": origin, "destination": destination}
        )

    async def place_search(
        self, query: str, scope: str = "1", page_size: int = 10, page_num: int = 0
    ) -> dict:
        """POI search"""
        return await self.call(
            "/place/v2/search",
            params={"query": query, "scope": scope, "page_size": page_size, "page_num": page_num},
        )


def create_map_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {"amap": AMapAdapter, "baidu": BaiduMapAdapter}
    if provider not in providers:
        raise ValueError(f"Unsupported map provider: {provider}")
    return providers[provider](config)
