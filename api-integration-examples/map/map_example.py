"""
地图 API 集成示例代码
功能：地理编码、逆地理编码、路径规划、地点搜索
支持：高德地图、百度地图
"""

from typing import Optional, List, Tuple
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import hashlib
import time
from datetime import datetime

load_dotenv()

# 高德地图配置
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "your-amap-key")
AMAP_SECRET = os.getenv("AMAP_SECRET", "your-amap-secret")

# 百度地图配置
BAIDU_MAP_API_KEY = os.getenv("BAIDU_MAP_API_KEY", "your-baidu-key")
BAIDU_MAP_SECRET = os.getenv("BAIDU_MAP_SECRET", "your-baidu-secret")


class Location(BaseModel):
    """位置信息"""
    latitude: float
    longitude: float
    address: Optional[str] = None
    formatted_address: Optional[str] = None


class GeoResponse(BaseModel):
    """地理编码响应"""
    success: bool
    location: Optional[Location] = None
    message: str
    provider: str


class RouteResponse(BaseModel):
    """路径规划响应"""
    success: bool
    distance: float  # 距离（米）
    duration: float  # 时间（秒）
    steps: Optional[List[dict]] = None
    message: str
    provider: str


# ============ 高德地图 ============

class AmapClient:
    """高德地图客户端"""
    
    def __init__(self):
        self.api_key = AMAP_API_KEY
        self.secret = AMAP_SECRET
        self.base_url = "https://restapi.amap.com/v3"
    
    def _generate_signature(self, params: dict) -> str:
        """生成高德签名"""
        # 排序参数
        sorted_params = sorted(params.items())
        # 构建待签名字符串
        sign_str = "&".join([f"{k}={v}" for k, v in sorted_params])
        # 添加 secret
        sign_str += self.secret
        # MD5 签名
        return hashlib.md5(sign_str.encode("utf-8")).hexdigest()
    
    def geocode(self, address: str, city: Optional[str] = None) -> GeoResponse:
        """
        地理编码（地址转坐标）
        
        Args:
            address: 地址
            city: 城市（可选）
        
        Returns:
            地理编码响应
        """
        params = {
            "key": self.api_key,
            "address": address,
            "output": "json"
        }
        
        if city:
            params["city"] = city
        
        # 生成签名
        params["sig"] = self._generate_signature(params)
        
        url = f"{self.base_url}/geocode/geo"
        
        print(f"高德地理编码:")
        print(f"  地址：{address}")
        print(f"  城市：{city or '全国'}")
        print(f"  URL: {url}")
        print()
        
        # 模拟响应
        return GeoResponse(
            success=True,
            location=Location(
                latitude=39.9042,
                longitude=116.4074,
                address=address,
                formatted_address="北京市朝阳区"
            ),
            message="成功",
            provider="amap"
        )
    
    def reverse_geocode(
        self,
        latitude: float,
        longitude: float
    ) -> GeoResponse:
        """
        逆地理编码（坐标转地址）
        
        Args:
            latitude: 纬度
            longitude: 经度
        
        Returns:
            地理编码响应
        """
        params = {
            "key": self.api_key,
            "location": f"{longitude},{latitude}",
            "output": "json"
        }
        
        params["sig"] = self._generate_signature(params)
        
        print(f"高德逆地理编码:")
        print(f"  坐标：{latitude}, {longitude}")
        print()
        
        return GeoResponse(
            success=True,
            location=Location(
                latitude=latitude,
                longitude=longitude,
                formatted_address="北京市朝阳区某某街道"
            ),
            message="成功",
            provider="amap"
        )
    
    def route_planning(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        mode: str = "driving"
    ) -> RouteResponse:
        """
        路径规划
        
        Args:
            origin: 起点坐标 (lat, lng)
            destination: 终点坐标 (lat, lng)
            mode: 出行方式（driving/walking/transit）
        
        Returns:
            路径规划响应
        """
        params = {
            "key": self.api_key,
            "origin": f"{origin[1]},{origin[0]}",
            "destination": f"{destination[1]},{destination[0]}",
            "output": "json"
        }
        
        params["sig"] = self._generate_signature(params)
        
        print(f"高德路径规划:")
        print(f"  起点：{origin}")
        print(f"  终点：{destination}")
        print(f"  方式：{mode}")
        print()
        
        return RouteResponse(
            success=True,
            distance=15000.5,
            duration=1800.0,
            message="成功",
            provider="amap"
        )
    
    def search_places(
        self,
        keywords: str,
        location: Optional[Tuple[float, float]] = None,
        radius: int = 1000
    ) -> List[dict]:
        """
        地点搜索
        
        Args:
            keywords: 搜索关键词
            location: 中心点坐标（可选）
            radius: 搜索半径（米）
        
        Returns:
            地点列表
        """
        params = {
            "key": self.api_key,
            "keywords": keywords,
            "output": "json"
        }
        
        if location:
            params["location"] = f"{location[1]},{location[0]}"
            params["radius"] = str(radius)
        
        params["sig"] = self._generate_signature(params)
        
        print(f"高德地点搜索:")
        print(f"  关键词：{keywords}")
        print(f"  位置：{location}")
        print(f"  半径：{radius}米")
        print()
        
        # 模拟返回
        return [
            {
                "name": "某某餐厅",
                "address": "北京市朝阳区某某路 1 号",
                "location": "116.4074,39.9042",
                "type": "餐饮服务"
            },
            {
                "name": "某某酒店",
                "address": "北京市朝阳区某某路 2 号",
                "location": "116.4084,39.9052",
                "type": "住宿服务"
            }
        ]


# ============ 百度地图 ============

class BaiduMapClient:
    """百度地图客户端"""
    
    def __init__(self):
        self.api_key = BAIDU_MAP_API_KEY
        self.secret = BAIDU_MAP_SECRET
        self.base_url = "https://api.map.baidu.com"
    
    def geocode(self, address: str, city: Optional[str] = None) -> GeoResponse:
        """地理编码"""
        params = {
            "address": address,
            "output": "json",
            "ak": self.api_key
        }
        
        if city:
            params["city"] = city
        
        print(f"百度地理编码:")
        print(f"  地址：{address}")
        print()
        
        return GeoResponse(
            success=True,
            location=Location(
                latitude=39.9042,
                longitude=116.4074,
                address=address
            ),
            message="成功",
            provider="baidu"
        )
    
    def reverse_geocode(
        self,
        latitude: float,
        longitude: float
    ) -> GeoResponse:
        """逆地理编码"""
        params = {
            "location": f"{latitude},{longitude}",
            "output": "json",
            "ak": self.api_key
        }
        
        print(f"百度逆地理编码:")
        print(f"  坐标：{latitude}, {longitude}")
        print()
        
        return GeoResponse(
            success=True,
            location=Location(
                latitude=latitude,
                longitude=longitude,
                formatted_address="北京市朝阳区某某街道"
            ),
            message="成功",
            provider="baidu"
        )
    
    def route_planning(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        mode: str = "driving"
    ) -> RouteResponse:
        """路径规划"""
        print(f"百度路径规划:")
        print(f"  起点：{origin}")
        print(f"  终点：{destination}")
        print()
        
        return RouteResponse(
            success=True,
            distance=15500.0,
            duration=1850.0,
            message="成功",
            provider="baidu"
        )


# ============ 统一地图服务 ============

class MapService:
    """统一地图服务"""
    
    def __init__(self, provider: str = "amap"):
        self.provider = provider
        if provider == "amap":
            self.client = AmapClient()
        elif provider == "baidu":
            self.client = BaiduMapClient()
        else:
            raise ValueError(f"不支持的服务商：{provider}")
    
    def geocode(self, address: str) -> GeoResponse:
        """地理编码"""
        return self.client.geocode(address)
    
    def reverse_geocode(self, lat: float, lng: float) -> GeoResponse:
        """逆地理编码"""
        return self.client.reverse_geocode(lat, lng)
    
    def route(self, origin: Tuple[float, float], dest: Tuple[float, float]) -> RouteResponse:
        """路径规划"""
        return self.client.route_planning(origin, dest)


# ============ 使用示例 ============

def example_map():
    """地图 API 示例"""
    print("=== 地图 API 示例 ===\n")
    
    # 1. 高德地理编码
    print("1. 高德地理编码:")
    amap = AmapClient()
    response = amap.geocode("北京市朝阳区天安门")
    print(f"   地址：北京市朝阳区天安门")
    print(f"   坐标：{response.location.latitude}, {response.location.longitude}")
    print(f"   格式化地址：{response.location.formatted_address}\n")
    
    # 2. 高德逆地理编码
    print("2. 高德逆地理编码:")
    response = amap.reverse_geocode(39.9042, 116.4074)
    print(f"   坐标：39.9042, 116.4074")
    print(f"   地址：{response.location.formatted_address}\n")
    
    # 3. 高德路径规划
    print("3. 高德路径规划:")
    response = amap.route_planning(
        origin=(39.9042, 116.4074),
        destination=(39.9142, 116.4174)
    )
    print(f"   距离：{response.distance / 1000:.2f} 公里")
    print(f"   时间：{response.duration / 60:.1f} 分钟\n")
    
    # 4. 高德地点搜索
    print("4. 高德地点搜索:")
    places = amap.search_places("餐厅", location=(39.9042, 116.4074))
    for place in places:
        print(f"   - {place['name']} ({place['type']})")
    print()
    
    # 5. 百度地图
    print("5. 百度地图:")
    baidu = BaiduMapClient()
    response = baidu.geocode("北京市海淀区中关村")
    print(f"   地址：北京市海淀区中关村")
    print(f"   坐标：{response.location.latitude}, {response.location.longitude}\n")
    
    # 6. 统一地图服务
    print("6. 统一地图服务:")
    map_service = MapService(provider="amap")
    response = map_service.geocode("上海市浦东新区")
    print(f"   服务商：{map_service.provider}")
    print(f"   地址：上海市浦东新区")
    print(f"   坐标：{response.location.latitude}, {response.location.longitude}")


if __name__ == "__main__":
    example_map()
