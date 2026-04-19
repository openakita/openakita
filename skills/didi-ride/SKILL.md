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

# Didi Ride

Via DiDi MCP Server API Provides,, Manage. 

## Quick Start

### Get MCP KEY

https://mcp.didichuxing.com/claw Get MCP Key, orUse App. 

### Configuration Key

in Agent MCP Key, orEdit: 
export DIDI_MCP_KEY="your_key"

### Dependency

npm install -g mcporter

## Core Capabilities

-: "[]", "", ""
-: A B
-: 
-: in, 
-: 15, 9
-: ///
-: 

##

1.: maps_textsearch
2.
3.: taxi_estimate (Get traceId) 
4. Create: taxi_create_order
5.: taxi_query_order

## Tool

| | |
|------|------|
| | maps_textsearch, taxi_estimate, taxi_create_order, taxi_query_order, taxi_cancel_order |
| | maps_direction_driving, maps_direction_transit, maps_direction_walking, maps_direction_bicycling |
| | maps_place_around |

## MCP Call

MCP_URL="https://mcp.didichuxing.com/mcp-servers?key=$DIDI_MCP_KEY"
mcporter call "$MCP_URL" <tool> --args '{"key":"value"}'

## Pre-built Scripts

### scripts/didi_mcp.py
MCP, Set DIDI_MCP_KEY. 

```bash
python3 scripts/didi_mcp.py ride --from "SOHO" --to ""
python3 scripts/didi_mcp.py route --from "" --to ""
python3 scripts/didi_mcp.py poi --keyword "" --location "116.4,39.9"
python3 scripts/didi_mcp.py price --from "" --to ""
```