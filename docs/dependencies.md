# OpenAkita 完整依赖清单

> 自动扫描生成于 2026-01-31

## 📊 依赖统计

| 类型 | 数量 |
|------|------|
| Python 第三方库 | 16 |
| Python 标准库 | 26 |
| 系统工具 | 3 |
| 可选 IM 通道依赖 | 1 |

---

## 🐍 Python 第三方依赖

### 核心 LLM

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `anthropic` | >=0.40.0 | Claude API 官方客户端 | `core/brain.py` |
| `openai` | >=1.0.0 | OpenAI 兼容 API (备用端点) | `core/brain.py` |

### MCP 协议

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `mcp` | >=1.0.0 | Model Context Protocol | `tools/mcp.py` |

### CLI 和用户界面

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `rich` | >=13.7.0 | 终端富文本输出、进度条、表格 | `main.py` |
| `prompt-toolkit` | >=3.0.43 | 交互式命令行输入 | `main.py` |
| `typer` | >=0.12.0 | CLI 框架 | `main.py` |

### 异步和 HTTP

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `httpx` | >=0.27.0 | 异步 HTTP 客户端 | `tools/web.py`, `channels/adapters/*` |
| `aiofiles` | >=24.1.0 | 异步文件操作 | `tools/file.py` |

### 数据库

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `aiosqlite` | >=0.20.0 | 异步 SQLite | `storage/database.py` |

### 数据验证

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `pydantic` | >=2.5.0 | 数据模型验证 | 全局 |
| `pydantic-settings` | >=2.1.0 | 配置管理 | `config.py` |

### Git 操作

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `gitpython` | >=3.1.40 | Git 仓库操作 | `evolution/*` |

### 浏览器自动化

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `playwright` | >=1.40.0 | 浏览器自动化 | `tools/browser_mcp.py` |

### 配置文件

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `pyyaml` | >=6.0.1 | YAML 解析 | `skills/parser.py` |
| `python-dotenv` | >=1.0.0 | 环境变量加载 | `config.py` |

### 工具库

| 包名 | 版本要求 | 用途 | 使用位置 |
|------|---------|------|---------|
| `tenacity` | >=8.2.3 | 重试机制 | `core/brain.py` |

---

## 📦 Python 标准库 (内置)

这些是 Python 自带的模块，无需单独安装：

### 异步编程
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `asyncio` | 异步 I/O | 全局 |

### 数据处理
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `json` | JSON 编解码 | 全局 |
| `re` | 正则表达式 | `core/memory.py`, `skills/parser.py` |
| `uuid` | UUID 生成 | `core/agent.py`, `sessions/*` |
| `base64` | Base64 编解码 | `tools/browser_mcp.py`, `channels/adapters/*` |
| `hashlib` | 哈希算法 | `channels/adapters/*`, `channels/media/*` |
| `hmac` | 消息认证码 | `channels/adapters/dingtalk.py` |

### 系统接口
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `os` | 操作系统接口 | `tools/shell.py`, `tools/file.py` |
| `sys` | 系统参数和函数 | `main.py` |
| `subprocess` | 子进程管理 | `tools/shell.py`, `skills/loader.py` |
| `shutil` | 高级文件操作 | `tools/file.py`, `channels/media/storage.py` |
| `mimetypes` | MIME 类型 | `channels/media/handler.py` |

### 路径和文件
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `pathlib` | 面向对象路径 | 全局 |

### 时间日期
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `datetime` | 日期时间处理 | 全局 |
| `time` | 时间函数 | `core/brain.py`, `channels/adapters/*` |

### 类型系统
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `typing` | 类型提示 | 全局 |
| `dataclasses` | 数据类装饰器 | 全局 |
| `enum` | 枚举类型 | `channels/types.py`, `scheduler/task.py` |
| `abc` | 抽象基类 | `channels/base.py`, `scheduler/triggers.py` |

### 日志
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `logging` | 日志系统 | 全局 |

### XML 处理
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `xml.etree.ElementTree` | XML 解析 | `channels/adapters/wework.py` |

### 命令行
| 模块 | 用途 | 使用位置 |
|------|------|---------|
| `argparse` | 命令行参数解析 | `evolution/generator.py` |

---

## 🔧 系统工具依赖

| 工具 | 版本要求 | 用途 | 检查命令 |
|------|---------|------|---------|
| **Python** | >=3.11 | 运行环境 | `python --version` |
| **Git** | >=2.30 | 版本控制、GitPython 后端 | `git --version` |
| **浏览器内核** | - | Playwright 需要 | `playwright install` |

---

## 📱 可选依赖 - IM 通道

根据你使用的 IM 平台，安装对应依赖：

### Telegram

| 包名 | 版本要求 | 用途 |
|------|---------|------|
| `python-telegram-bot` | >=21.0 | Telegram Bot API |

安装命令：
```bash
pip install python-telegram-bot>=21.0
```

### 飞书 / 企业微信 / 钉钉

这些平台使用 `httpx` 作为 HTTP 客户端，已包含在核心依赖中，无需额外安装。

### QQ 官方机器人

```bash
pip install qq-botpy>=1.0
```

### OneBot（通用协议）

如果使用 WebSocket 协议：
```bash
pip install websockets>=12.0
```

---

## 🧪 开发依赖 (可选)

用于开发和测试：

| 包名 | 版本要求 | 用途 |
|------|---------|------|
| `pytest` | >=8.0.0 | 测试框架 |
| `pytest-asyncio` | >=0.23.0 | 异步测试支持 |
| `pytest-cov` | >=4.1.0 | 测试覆盖率 |
| `ruff` | >=0.1.9 | 代码检查和格式化 |
| `mypy` | >=1.8.0 | 静态类型检查 |

安装命令：
```bash
pip install -e ".[dev]"
```

---

## 📁 文件结构

```
openakita/
├── requirements.txt        # 依赖列表 (pip 格式)
├── pyproject.toml          # 项目配置 (标准格式)
├── docs/
│   ├── dependencies.md     # 本文档
│   └── deploy.md           # 部署文档
└── scripts/
    ├── deploy.ps1          # Windows 一键部署脚本
    └── deploy.sh           # Linux/macOS 一键部署脚本
```

---

## 🔍 依赖扫描结果

以下是从源代码中扫描的所有 import 语句汇总：

### 第三方库 import

```python
from anthropic import Anthropic
from anthropic.types import Message, MessageParam, ToolParam
from openai import OpenAI
import httpx
import aiofiles
import aiofiles.os
import aiosqlite
from pydantic_settings import BaseSettings
from pydantic import Field
import yaml
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from telegram import Bot, Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters
```

### 标准库 import

```python
import asyncio
import logging
import json
import uuid
import os
import sys
import subprocess
import shutil
import re
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Awaitable, AsyncIterator, TYPE_CHECKING
from enum import Enum
from abc import ABC, abstractmethod
import mimetypes
import hashlib
import hmac
import base64
import time
import xml.etree.ElementTree as ET
import argparse
```

---

*此文档由自动扫描生成，如有遗漏请提交 Issue*
