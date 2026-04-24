---
name: openakita/skills@xiaodu-control
description: "Xiaodu smart device control skill via MCP protocol. Control Xiaodu devices and ecosystem hardware for smart home IoT tasks, scene automation, and physical interaction. Use when user wants to control smart home devices or IoT equipment."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# 小度 (Xiaodu)

通过小度 MCP 协议赋予智能体物理交互能力，实现对小度设备及生态硬件的精准控制、场景联动与家庭 IoT 任务执行。

## 功能

- 智能设备控制
- 场景联动
- 家庭 IoT 任务
- 生态硬件管理

## 预置脚本

### scripts/xiaodu_mcp.py
小度设备控制 MCP 客户端（MCP URL 需配置），需设置 XIAODU_MCP_KEY。

```bash
python3 scripts/xiaodu_mcp.py devices
python3 scripts/xiaodu_mcp.py control --device light-001 --action on
python3 scripts/xiaodu_mcp.py scene --name "回家模式"
```

