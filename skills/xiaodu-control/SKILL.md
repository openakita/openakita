---
name: openakita/skills@xiaodu-control
description: "Xiaodu smart device control skill via MCP protocol. Control Xiaodu devices and ecosystem hardware for smart home IoT tasks, scene automation, and physical interaction. Use when user wants to control smart home devices or IoT equipment."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# Xiaodu (Baidu Smart Speaker)

Empowers the agent with physical interaction capabilities through the Xiaodu MCP protocol, enabling precise control of Xiaodu devices and ecosystem hardware, scene linkage, and home IoT task execution.

## Features

- Smart device control
- Scene automation
- Home IoT tasks
- Ecosystem hardware management

## Pre-built Scripts

### scripts/xiaodu_mcp.py
Xiaodu device control MCP client (MCP URL must be configured). Requires `XIAODU_MCP_KEY`.

```bash
python3 scripts/xiaodu_mcp.py devices
python3 scripts/xiaodu_mcp.py control --device light-001 --action on
python3 scripts/xiaodu_mcp.py scene --name "Home Mode"
```
