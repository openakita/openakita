---
name: openakita/skills@amap-maps
description: "Amap (Gaode) Maps comprehensive service for POI search, route planning, travel planning, nearby search, heatmap visualization, and geocoding. Use when user wants to search locations, plan routes, find nearby places, or visualize geographic data."
license: MIT
metadata:
  author: AMap-Web
  version: "2.0.0"
requires:
  env: [AMAP_WEBSERVICE_KEY]
---

# Amap Maps综合服务

高德地图综合服务，包括地点搜索、路径规划、旅游规划和数据可视化等功能。

## 首次配置

访问高德开放平台 https://lbs.amap.com/api/webservice/create-project-and-key 创建应用并获取 Key。
设置环境变量：export AMAP_WEBSERVICE_KEY=your_key

## 场景一：关键词搜索

URL: https://www.amap.com/search?query={关键词}

## 场景二：周边搜索

先通过地理编码 API 获取坐标：
curl -s "https://restapi.amap.com/v3/geocode/geo?address={位置}&output=JSON&key={key}"

然后拼接周边搜索链接：
https://ditu.amap.com/search?query={类别}&query_type=RQBXY&longitude={经度}&latitude={纬度}&range=1000

## 场景三：POI 详细搜索

node scripts/poi-search.js --keywords=肯德基 --city=北京

## 场景四：路径规划

node scripts/route-planning.js --type=walking --origin=116.397428,39.90923 --destination=116.427281,39.903719

支持：walking（步行）、driving（驾车）、riding（骑行）、transfer（公交）

## 场景五：旅游规划

node scripts/travel-planner.js --city=北京 --interests=景点,美食,酒店

## 场景六：热力图

http://a.amap.com/jsapi_demo_show/static/openclaw/heatmap.html?mapStyle=grey&dataUrl={编码后数据地址}

## Pre-built Scripts

### scripts/amap_tool.py
高德地图 Web 服务 Python 封装，需设置 AMAP_WEBSERVICE_KEY。

```bash
python3 scripts/amap_tool.py geocode "北京市海淀区上地十街10号"
python3 scripts/amap_tool.py poi "咖啡" --city 北京
python3 scripts/amap_tool.py drive --from "天安门" --to "首都机场"
python3 scripts/amap_tool.py weather --city 110000
```
