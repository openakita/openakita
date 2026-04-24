#!/usr/bin/env python3
"""滴滴出行 MCP 客户端。

通过 JSON-RPC 2.0 over HTTP 与滴滴 MCP 服务器通信。
认证: DIDI_MCP_KEY 环境变量
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

MCP_URL_TEMPLATE = "https://mcp.didichuxing.com/mcp-servers?key={key}"


def get_mcp_url() -> str:
    key = os.environ.get("DIDI_MCP_KEY", "")
    if not key:
        print("错误: 请设置环境变量 DIDI_MCP_KEY", file=sys.stderr)
        sys.exit(1)
    return MCP_URL_TEMPLATE.format(key=key)


def jsonrpc_call(url: str, method: str, params: dict | None = None,
                 req_id: int = 1) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": req_id,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        print(f"HTTP {e.code}: {err_body}", file=sys.stderr)
        sys.exit(1)


def mcp_initialize(url: str) -> dict:
    return jsonrpc_call(url, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "openakita-didi", "version": "1.0.0"},
    }, req_id=0)


def mcp_tool_call(url: str, tool_name: str, arguments: dict) -> dict:
    return jsonrpc_call(url, "tools/call", {
        "name": tool_name,
        "arguments": arguments,
    })


def cmd_ride(args):
    url = get_mcp_url()
    mcp_initialize(url)
    result = mcp_tool_call(url, "create_ride", {
        "from_address": args.from_addr,
        "to_address": args.to_addr,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_route(args):
    url = get_mcp_url()
    mcp_initialize(url)
    result = mcp_tool_call(url, "route_plan", {
        "from_address": args.from_addr,
        "to_address": args.to_addr,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_poi(args):
    url = get_mcp_url()
    mcp_initialize(url)
    result = mcp_tool_call(url, "poi_search", {
        "keyword": args.keyword,
        "location": args.location or "",
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_price(args):
    url = get_mcp_url()
    mcp_initialize(url)
    result = mcp_tool_call(url, "estimate_price", {
        "from_address": args.from_addr,
        "to_address": args.to_addr,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="滴滴出行 MCP 客户端 (env: DIDI_MCP_KEY)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ride = sub.add_parser("ride", help="叫车")
    p_ride.add_argument("--from", dest="from_addr", required=True, help="出发地址")
    p_ride.add_argument("--to", dest="to_addr", required=True, help="目的地址")

    p_route = sub.add_parser("route", help="路线规划")
    p_route.add_argument("--from", dest="from_addr", required=True, help="出发地址")
    p_route.add_argument("--to", dest="to_addr", required=True, help="目的地址")

    p_poi = sub.add_parser("poi", help="周边搜索")
    p_poi.add_argument("--keyword", required=True, help="搜索关键词")
    p_poi.add_argument("--location", help="中心位置 (经度,纬度)")

    p_price = sub.add_parser("price", help="预估价格")
    p_price.add_argument("--from", dest="from_addr", required=True, help="出发地址")
    p_price.add_argument("--to", dest="to_addr", required=True, help="目的地址")

    args = parser.parse_args()
    {"ride": cmd_ride, "route": cmd_route,
     "poi": cmd_poi, "price": cmd_price}[args.command](args)


if __name__ == "__main__":
    main()

