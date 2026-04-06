---
name: openakita/skills@baidu-maps
description: "Baidu Maps skill for location search, nearby place search, geocoding, route planning, and weather queries. Use when user needs map services, location information, or navigation within China."
license: MIT
metadata:
  author: baidu
  version: "1.0.2"
---

# 百度地图

让 AI 像专家一样写地图代码，适用于出行、文旅、商业、智能车载等多场景。

## 功能

- 地址搜索：关键词搜索地点
- 附近搜索：基于位置搜索周边
- 地理编码：地址与坐标互转
- 路径规划：驾车/步行路线
- 天气查询：城市天气信息

## 配置

需要百度地图 Web 服务 API Key。

## 预置脚本

### scripts/baidu_maps.py
百度地图 Web 服务 API 封装，需设置 BAIDU_MAP_AK。

```bash
python3 scripts/baidu_maps.py geocode "北京市海淀区"
python3 scripts/baidu_maps.py poi "火锅" --region 成都
python3 scripts/baidu_maps.py route --origin 39.9,116.4 --dest 40.0,116.5
```
