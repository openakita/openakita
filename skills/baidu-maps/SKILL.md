---
name: openakita/skills@baidu-maps
description: "Baidu Maps skill for location search, nearby place search, geocoding, route planning, and weather queries. Use when user needs map services, location information, or navigation within China."
license: MIT
metadata:
  author: baidu
  version: "1.0.2"
---

# Baidu Maps

Enable AI to write map code like a professional, suitable for travel, culture and tourism, business, smart in-vehicle, and more scenarios.

## Features

- **Address Search**: Search places by keyword
- **Nearby Search**: Search surroundings based on location
- **Geocoding**: Convert between addresses and coordinates
- **Route Planning**: Driving/walking routes
- **Weather Query**: City weather information

## Configuration

Requires Baidu Maps Web Service API Key.

## Pre-built Scripts

### scripts/baidu_maps.py
Baidu Maps Web Service API wrapper. Requires BAIDU_MAP_AK to be set.

```bash
python3 scripts/baidu_maps.py geocode "Haidian District, Beijing"
python3 scripts/baidu_maps.py poi "Hotpot" --region Chengdu
python3 scripts/baidu_maps.py route --origin 39.9,116.4 --dest 40.0,116.5
```
