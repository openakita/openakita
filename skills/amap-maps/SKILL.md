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

# Amap Maps Comprehensive Service

Amap Maps comprehensive service, including POI search, route planning, travel planning, and data visualization features.

## Initial Configuration

Visit the Amap Open Platform at https://lbs.amap.com/api/webservice/create-project-and-key to create an application and obtain your API key.
Set the environment variable: export AMAP_WEBSERVICE_KEY=your_key

## Scenario 1: Keyword Search

URL: https://www.amap.com/search?query={keyword}

## Scenario 2: Nearby Search

First, obtain coordinates via the Geocoding API:
curl -s "https://restapi.amap.com/v3/geocode/geo?address={address}&output=JSON&key={key}"

Then construct the nearby search URL:
https://ditu.amap.com/search?query={category}&query_type=RQBXY&longitude={longitude}&latitude={latitude}&range=1000

## Scenario 3: POI Detailed Search

node scripts/poi-search.js --keywords=KFC --city=Beijing

## Scenario 4: Route Planning

node scripts/route-planning.js --type=walking --origin=116.397428,39.90923 --destination=116.427281,39.903719

Supported modes: walking, driving, riding, transfer (public transit)

## Scenario 5: Travel Planning

node scripts/travel-planner.js --city=Beijing --interests=attractions,food,hotels

## Scenario 6: Heatmap

http://a.amap.com/jsapi_demo_show/static/openclaw/heatmap.html?mapStyle=grey&dataUrl={encoded_data_url}

## Pre-built Scripts

### scripts/amap_tool.py
Python wrapper for Amap Web Services. Requires AMAP_WEBSERVICE_KEY environment variable.

```bash
python3 scripts/amap_tool.py geocode "10 Shangdi 10th Street, Haidian District, Beijing"
python3 scripts/amap_tool.py poi "coffee" --city Beijing
python3 scripts/amap_tool.py drive --from "Tiananmen" --to "Capital Airport"
python3 scripts/amap_tool.py weather --city 110000
```
