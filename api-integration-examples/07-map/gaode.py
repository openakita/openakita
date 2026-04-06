"""
API 集成示例 7: 高德地图
"""
import requests

class GaodeMapClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://restapi.amap.com/v3"
    
    def geocode(self, address):
        """地址解析"""
        params = {
            "key": self.api_key,
            "address": address
        }
        response = requests.get(f"{self.base_url}/geocode/geo", params=params)
        return response.json()
    
    def regeocode(self, location):
        """逆地址解析"""
        params = {
            "key": self.api_key,
            "location": location  # 格式：经度，纬度
        }
        response = requests.get(f"{self.base_url}/geocode/regeo", params=params)
        return response.json()
    
    def route_driving(self, origin, destination):
        """路径规划 (驾车)"""
        params = {
            "key": self.api_key,
            "origin": origin,  # 格式：经度，纬度
            "destination": destination
        }
        response = requests.get(f"{self.base_url}/direction/driving", params=params)
        return response.json()
    
    def weather(self, city):
        """天气查询"""
        params = {
            "key": self.api_key,
            "city": city,  # 城市编码
            "extensions": "all"
        }
        response = requests.get(f"{self.base_url}/weather/weatherInfo", params=params)
        return response.json()
    
    def ip_location(self, ip):
        """IP 定位"""
        params = {
            "key": self.api_key,
            "ip": ip
        }
        response = requests.get(f"{self.base_url}/ip", params=params)
        return response.json()

# 使用示例
if __name__ == "__main__":
    gaode = GaodeMapClient("your_api_key")
    # result = gaode.geocode("北京市朝阳区")
    # print(result)
    print("高德地图 API 示例已就绪")
