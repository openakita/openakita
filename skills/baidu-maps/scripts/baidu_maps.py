#!/usr/bin/env python3
"""百度地图 Web 服务 API 封装 CLI 工具。

支持地理编码、逆地理编码、POI 搜索、路径规划。

环境变量:
    BAIDU_MAP_AK  百度地图服务端 AK

示例:
    python3 baidu_maps.py geocode "北京市海淀区上地十街10号"
    python3 baidu_maps.py reverse 39.9042 116.4074
    python3 baidu_maps.py poi "咖啡" --region 北京
    python3 baidu_maps.py route 39.915,116.404 31.230,121.474
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.map.baidu.com"


def _get(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{BASE}{path}?{qs}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def geocode(ak: str, address: str) -> dict:
    return _get("/geocoding/v3/", {
        "address": address,
        "output": "json",
        "ak": ak,
    })


def reverse_geocode(ak: str, lat: float, lng: float) -> dict:
    return _get("/reverse_geocoding/v3/", {
        "ak": ak,
        "output": "json",
        "location": f"{lat},{lng}",
    })


def poi_search(ak: str, query: str, region: str, page_size: int = 10,
               page_num: int = 0) -> dict:
    return _get("/place/v2/search", {
        "query": query,
        "region": region,
        "output": "json",
        "ak": ak,
        "page_size": page_size,
        "page_num": page_num,
    })


def route_driving(ak: str, origin: str, destination: str) -> dict:
    return _get("/direction/v2/driving", {
        "origin": origin,
        "destination": destination,
        "ak": ak,
    })


def main():
    parser = argparse.ArgumentParser(
        description="百度地图 Web 服务 API CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python3 baidu_maps.py geocode \"北京市海淀区上地十街10号\"\n"
               "  python3 baidu_maps.py reverse 39.9042 116.4074\n"
               "  python3 baidu_maps.py poi \"咖啡\" --region 北京\n"
               "  python3 baidu_maps.py route 39.915,116.404 31.230,121.474",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # geocode
    p_geo = sub.add_parser("geocode", help="地理编码（地址 → 经纬度）")
    p_geo.add_argument("address", help="地址字符串")

    # reverse
    p_rev = sub.add_parser("reverse", help="逆地理编码（经纬度 → 地址）")
    p_rev.add_argument("lat", type=float, help="纬度")
    p_rev.add_argument("lng", type=float, help="经度")

    # poi
    p_poi = sub.add_parser("poi", help="POI 搜索")
    p_poi.add_argument("query", help="搜索关键词")
    p_poi.add_argument("--region", required=True, help="搜索区域（如 北京）")
    p_poi.add_argument("--page-size", type=int, default=10, help="每页数量 (默认 10)")
    p_poi.add_argument("--page-num", type=int, default=0, help="页码 (默认 0)")

    # route
    p_route = sub.add_parser("route", help="驾车路径规划")
    p_route.add_argument("origin", help="起点 lat,lng")
    p_route.add_argument("destination", help="终点 lat,lng")

    args = parser.parse_args()

    ak = os.environ.get("BAIDU_MAP_AK", "")
    if not ak:
        print("错误: 请设置环境变量 BAIDU_MAP_AK", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "geocode":
            result = geocode(ak, args.address)
        elif args.command == "reverse":
            result = reverse_geocode(ak, args.lat, args.lng)
        elif args.command == "poi":
            result = poi_search(ak, args.query, args.region,
                                args.page_size, args.page_num)
        elif args.command == "route":
            result = route_driving(ak, args.origin, args.destination)
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(json.dumps({"error": str(e), "detail": body}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

