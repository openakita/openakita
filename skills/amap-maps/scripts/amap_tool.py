#!/usr/bin/env python3
"""高德地图 Web 服务 API 封装 — 纯 stdlib 实现"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

API_BASE = "https://restapi.amap.com/v3"


def _key():
    k = os.environ.get("AMAP_WEBSERVICE_KEY", "")
    if not k:
        print(json.dumps({"error": "请设置环境变量 AMAP_WEBSERVICE_KEY"}, ensure_ascii=False))
        sys.exit(1)
    return k


def _get(path: str, params: dict) -> dict:
    params["key"] = _key()
    qs = urllib.parse.urlencode(params)
    url = f"{API_BASE}/{path}?{qs}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode()}
    except urllib.error.URLError as e:
        return {"error": str(e.reason)}


def _geocode(address: str) -> str | None:
    """地理编码：地址 -> 经纬度字符串 'lng,lat'"""
    result = _get("geocode/geo", {"address": address})
    geocodes = result.get("geocodes", [])
    if geocodes:
        return geocodes[0].get("location")
    return None


def cmd_geocode(args):
    result = _get("geocode/geo", {"address": args.address})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_reverse(args):
    result = _get("geocode/regeo", {"location": f"{args.lng},{args.lat}"})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_poi(args):
    params = {"keywords": args.keywords}
    if args.city:
        params["city"] = args.city
    result = _get("place/text", params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_around(args):
    params = {
        "location": f"{args.lng},{args.lat}",
        "keywords": args.keywords or "",
    }
    result = _get("place/around", params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_drive(args):
    origin = args.origin
    dest = args.dest

    if not _looks_like_coord(origin):
        loc = _geocode(origin)
        if not loc:
            print(json.dumps({"error": f"无法解析起点地址: {origin}"}, ensure_ascii=False, indent=2))
            return
        origin = loc

    if not _looks_like_coord(dest):
        loc = _geocode(dest)
        if not loc:
            print(json.dumps({"error": f"无法解析终点地址: {dest}"}, ensure_ascii=False, indent=2))
            return
        dest = loc

    result = _get("direction/driving", {"origin": origin, "destination": dest})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_walk(args):
    origin = args.origin
    dest = args.dest

    if not _looks_like_coord(origin):
        loc = _geocode(origin)
        if not loc:
            print(json.dumps({"error": f"无法解析起点地址: {origin}"}, ensure_ascii=False, indent=2))
            return
        origin = loc

    if not _looks_like_coord(dest):
        loc = _geocode(dest)
        if not loc:
            print(json.dumps({"error": f"无法解析终点地址: {dest}"}, ensure_ascii=False, indent=2))
            return
        dest = loc

    result = _get("direction/walking", {"origin": origin, "destination": dest})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_weather(args):
    result = _get("weather/weatherInfo", {"city": args.city})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _looks_like_coord(s: str) -> bool:
    """简单判断字符串是否看起来像 'lng,lat' 坐标"""
    parts = s.split(",")
    if len(parts) != 2:
        return False
    try:
        float(parts[0])
        float(parts[1])
        return True
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description="高德地图 Web 服务 API CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("geocode", help="地理编码（地址 → 坐标）")
    p.add_argument("address", help="地址")

    p = sub.add_parser("reverse", help="逆地理编码（坐标 → 地址）")
    p.add_argument("lng", help="经度")
    p.add_argument("lat", help="纬度")

    p = sub.add_parser("poi", help="POI 搜索")
    p.add_argument("keywords", help="搜索关键词")
    p.add_argument("--city", default="", help="城市名称")

    p = sub.add_parser("around", help="周边搜索")
    p.add_argument("lng", help="经度")
    p.add_argument("lat", help="纬度")
    p.add_argument("--keywords", default="", help="搜索关键词")

    p = sub.add_parser("drive", help="驾车路径规划")
    p.add_argument("--from", dest="origin", required=True, help="起点（坐标 lng,lat 或地址）")
    p.add_argument("--to", dest="dest", required=True, help="终点（坐标 lng,lat 或地址）")

    p = sub.add_parser("walk", help="步行路径规划")
    p.add_argument("--from", dest="origin", required=True, help="起点（坐标 lng,lat 或地址）")
    p.add_argument("--to", dest="dest", required=True, help="终点（坐标 lng,lat 或地址）")

    p = sub.add_parser("weather", help="天气查询")
    p.add_argument("city", help="城市编码或城市名称")

    args = parser.parse_args()
    dispatch = {
        "geocode": cmd_geocode,
        "reverse": cmd_reverse,
        "poi": cmd_poi,
        "around": cmd_around,
        "drive": cmd_drive,
        "walk": cmd_walk,
        "weather": cmd_weather,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
