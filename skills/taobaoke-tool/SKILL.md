---
name: openakita/skills@taobaoke-tool
description: "Taobaoke all-in-one toolkit for Taobao, JD, and Pinduoduo affiliate marketing. Convert product links to affiliate links, cross-platform price comparison, one-click price protection, and commission tracking. Use for e-commerce affiliate operations."
license: MIT
metadata:
  author: wuhaichao87
  version: "2.1.0"
requires:
  env: [ZHETAOKE_APP_KEY, ZHETAOKE_SID]
---

# 淘宝客全能工具箱

淘宝客一站式解决方案，支持链接转链、全网比价、自动价保、佣金追踪。

## Configuration

在环境变量中设置：
export ZHETAOKE_APP_KEY="your_key"
export ZHETAOKE_SID="your_sid"

可选配置：
export JD_UNION_ID="your_jd_id"
export TAOBAO_PID="your_pid"
export PDD_PID="your_pdd_pid"

## 支持平台

| 平台 | 支持格式 | 商品信息 | 佣金信息 |
|------|---------|---------|---------|
| 淘宝 | 淘口令、链接 | 完整 | 完整 |
| 京东 | 短链接、标准链接 | 完整 | 完整 |
| 拼多多 | mobile.yangkeduo.com | 基础 | 基础 |

## Core Features

- 智能转链：淘宝/京东/拼多多链接自动转佣金链接
- 全网比价：对比三大平台价格
- 一键价保：自动申请价保追回差价
- 佣金追踪：记录转链和成交数据
- 高佣转链：通过折淘客 API 获取最高佣金

## 使用

python3 scripts/taobaoke_master.py <链接>

## Pre-built Scripts

### scripts/taobaoke_master.py
淘宝客转链/搜索/比价工具，需设置 ZHETAOKE_APP_KEY 和 ZHETAOKE_SID。

```bash
python3 scripts/taobaoke_master.py convert "https://item.taobao.com/item.htm?id=123456"
python3 scripts/taobaoke_master.py search "无线蓝牙耳机"
python3 scripts/taobaoke_master.py compare "https://item.taobao.com/item.htm?id=123456"
```
