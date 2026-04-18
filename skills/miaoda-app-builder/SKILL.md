---
name: openakita/skills@miaoda-app-builder
description: "Miaoda App Builder - create web apps, WeChat mini-programs, games, AI tools, SaaS products, and dashboards through natural language conversations. Full workflow from code generation to deployment."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# 秒哒 (Miaoda)

支持通过自然语言对话完成应用的创建、查看、修改、发布上线等操作。

## 支持应用类型

- 网页应用
- 微信小程序
- 游戏
- AI 工具
- SaaS 产品
- 数据看板

## Installation

通过 ClawHub 安装：clawhub install miaoda-app-builder

## 使用

直接用自然语言描述想要创建的应用，秒哒会自动生成代码并部署。

## Pre-built Scripts

### scripts/miaoda.py
智能应用生成（百度千帆 AppBuilder），需设置 APPBUILDER_TOKEN。

```bash
python3 scripts/miaoda.py create "一个待办事项应用"
python3 scripts/miaoda.py chat "添加用户登录功能"
```
