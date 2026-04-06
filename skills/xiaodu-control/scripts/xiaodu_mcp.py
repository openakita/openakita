#!/usr/bin/env python3
"""小度设备控制 MCP 客户端（模板脚本，MCP URL 待配置）。

通过 JSON-RPC 2.0 over HTTP 与小度 MCP 服务器通信。
认证: XIAODU_MCP_KEY 环境变量
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

MCP_URL_TEMPLATE = os.environ.get(
    "XIAODU_MCP_URL",
    "https://mcp.xiaodu.baidu.com/mcp-servers?key={key}",
)


def get_mcp_url() -> str:
    key = os.environ.get("XIAODU_MCP_KEY", "")
    if not key:
        print("错误: 请设置环境变量 XIAODU_MCP_KEY", file=sys.stderr)
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
        "clientInfo": {"name": "openakita-xiaodu", "version": "1.0.0"},
    }, req_id=0)


def mcp_tool_call(url: str, tool_name: str, arguments: dict) -> dict:
    return jsonrpc_call(url, "tools/call", {
        "name": tool_name,
        "arguments": arguments,
    })


def cmd_devices(args):
    url = get_mcp_url()
    mcp_initialize(url)
    result = mcp_tool_call(url, "list_devices", {})
    if "result" in result:
        devices = result["result"]
        if isinstance(devices, list):
            for d in devices:
                status = d.get("status", "unknown")
                print(f"  {d.get('id', '?'):>12s}  [{status}]  {d.get('name', '')}")
            return
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_control(args):
    url = get_mcp_url()
    mcp_initialize(url)
    arguments = {"device_id": args.device, "action": args.action}
    if args.value:
        arguments["value"] = args.value
    result = mcp_tool_call(url, "control_device", arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_scene(args):
    url = get_mcp_url()
    mcp_initialize(url)
    result = mcp_tool_call(url, "execute_scene", {"scene_name": args.name})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="小度设备控制 MCP 客户端 (env: XIAODU_MCP_KEY, XIAODU_MCP_URL[可选])")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="列出设备")

    p_ctrl = sub.add_parser("control", help="控制设备")
    p_ctrl.add_argument("--device", required=True, help="设备 ID")
    p_ctrl.add_argument("--action", required=True, help="控制动作 (on/off/...)")
    p_ctrl.add_argument("--value", help="附加参数值")

    p_scene = sub.add_parser("scene", help="执行场景")
    p_scene.add_argument("--name", required=True, help="场景名称")

    args = parser.parse_args()
    {"devices": cmd_devices, "control": cmd_control,
     "scene": cmd_scene}[args.command](args)


if __name__ == "__main__":
    main()
