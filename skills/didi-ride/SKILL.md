---
name: openakita/skills@didi-ride
description: "DiDi ride-hailing service skill. Supports taxi booking, price estimation, route planning (driving/transit/walking/cycling), order management, driver location tracking, and scheduled rides. Use when user expresses any transportation need including ride-hailing, route queries, or commuting."
license: MIT
metadata:
  author: didi
  version: "1.0.0"
requires:
  env: [DIDI_MCP_KEY]
---

# Didi Ride服务

Via DiDi MCP Server API Provides打车、路线规划、订单Manage等出行能力。

## Quick Start

### Get MCP KEY

访问 https://mcp.didichuxing.com/claw Get MCP Key，或Use滴滴出行 App 扫码。

### Configuration Key

直接在对话中告诉 Agent 你的 MCP Key，或Edit配置：
export DIDI_MCP_KEY="your_key"

### 依赖

npm install -g mcporter

## Core Capabilities

- 打车：直接说"打车去[地点]"、"回家"、"上班"
- 查价：查一下从 A 到 B 多少钱
- 查询订单：了解当前订单状态
- 司机位置：司机在哪里、多久到
- 预约出行：15 分钟后打车、明天 9 点去机场
- 路线规划：驾车/公交/步行/骑行
- 取消订单：取消当前订单

## 主流程

1. 地址解析：maps_textsearch
2. 确认起终点
3. 价格预估：taxi_estimate（Get traceId）
4. Create订单：taxi_create_order
5. 查询状态：taxi_query_order

## 工具清单

| 领域 | 工具 |
|------|------|
| 打车 | maps_textsearch, taxi_estimate, taxi_create_order, taxi_query_order, taxi_cancel_order |
| 路线 | maps_direction_driving, maps_direction_transit, maps_direction_walking, maps_direction_bicycling |
| 周边 | maps_place_around |

## MCP Call格式

MCP_URL="https://mcp.didichuxing.com/mcp-servers?key=$DIDI_MCP_KEY"
mcporter call "$MCP_URL" <tool> --args '{"key":"value"}'

## Pre-built Scripts

### scripts/didi_mcp.py
滴滴出行 MCP 客户端，需Set DIDI_MCP_KEY。

```bash
python3 scripts/didi_mcp.py ride --from "望京SOHO" --to "国贸大厦"
python3 scripts/didi_mcp.py route --from "望京" --to "国贸"
python3 scripts/didi_mcp.py poi --keyword "加油站" --location "116.4,39.9"
python3 scripts/didi_mcp.py price --from "望京" --to "国贸"
```
