"""
地图 API 适配器
支持高德地图和百度地图
"""

import aiohttp
from typing import Any, Dict, List, Optional
from . import BaseAPIAdapter, APIError, AuthenticationError


class AMapAdapter(BaseAPIAdapter):
    """高德地图 API 适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.base_url = "https://restapi.amap.com/v3"
    
    async def authenticate(self) -> bool:
        return bool(self.api_key)
    
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
        params = kwargs.get('params', {})
        params['key'] = self.api_key
        
        async with aiohttp.ClientSession() as session:
            async with session.request(method, f"{self.base_url}{endpoint}", params=params) as response:
                result = await response.json()
                if result.get('status') != '1':
                    raise APIError(f"高德地图 API 错误：{result.get('info')}")
                return result
    
    async def geocode(self, address: str, city: Optional[str] = None) -> Dict:
        """地理编码：地址转坐标"""
        params = {'address': address}
        if city:
            params['city'] = city
        return await self.call("/geocode/geo", params=params)
    
    async def regeocode(self, location: str) -> Dict:
        """逆地理编码：坐标转地址"""
        return await self.call("/geocode/regeo", params={'location': location})
    
    async def weather(self, city: str, extensions: str = 'base') -> Dict:
        """获取天气信息"""
        return await self.call("/weather/weatherInfo", params={'city': city, 'extensions': extensions})
    
    async def direction_driving(self, origin: str, destination: str) -> Dict:
        """路径规划：驾车"""
        return await self.call("/direction/driving", params={'origin': origin, 'destination': destination})
    
    async def place_search(self, keywords: str, city: Optional[str] = None, types: Optional[str] = None) -> Dict:
        """POI 搜索"""
        params = {'keywords': keywords}
        if city:
            params['city'] = city
        if types:
            params['types'] = types
        return await self.call("/place/text", params=params)


class BaiduMapAdapter(BaseAPIAdapter):
    """百度地图 API 适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.base_url = "https://api.map.baidu.com"
    
    async def authenticate(self) -> bool:
        return bool(self.api_key)
    
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
        params = kwargs.get('params', {})
        params['ak'] = self.api_key
        params['output'] = 'json'
        
        async with aiohttp.ClientSession() as session:
            async with session.request(method, f"{self.base_url}{endpoint}", params=params) as response:
                result = await response.json()
                if result.get('status') != 0:
                    raise APIError(f"百度地图 API 错误：{result.get('message')}")
                return result
    
    async def geocode(self, address: str, city: Optional[str] = None) -> Dict:
        """地理编码"""
        params = {'address': address}
        if city:
            params['city'] = city
        return await self.call("/geocoding/v3/", params=params)
    
    async def regeocode(self, location: str) -> Dict:
        """逆地理编码"""
        return await self.call("/reverse_geocoding/v3/", params={'location': location})
    
    async def weather(self, location: str) -> Dict:
        """天气查询"""
        return await self.call("/weather/v1", params={'location': location})
    
    async def direction_driving(self, origin: str, destination: str) -> Dict:
        """驾车路径规划"""
        return await self.call("/direction/v2/driving", params={'origin': origin, 'destination': destination})
    
    async def place_search(self, query: str, scope: str = '1', page_size: int = 10, page_num: int = 0) -> Dict:
        """POI 检索"""
        return await self.call("/place/v2/search", params={
            'query': query, 'scope': scope, 'page_size': page_size, 'page_num': page_num
        })


def create_map_adapter(provider: str, config: Dict[str, Any]) -> BaseAPIAdapter:
    providers = {'amap': AMapAdapter, 'baidu': BaiduMapAdapter}
    if provider not in providers:
        raise ValueError(f"不支持的地图提供商：{provider}")
    return providers[provider](config)
