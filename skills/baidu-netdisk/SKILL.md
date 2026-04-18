---
name: openakita/skills@baidu-netdisk
description: "Baidu Netdisk (Baidu Cloud) file management skill. Upload, download, transfer, share, and list files. Use when user wants to manage files on Baidu Netdisk cloud storage."
license: MIT
metadata:
  author: baidu-netdisk
  version: "1.0.0"
---

# Baidu NetDisk

个人和企业的专属云端数字助手，文件上传下载、备份、分享、管理一句话搞定。

## Installation

npx skills add https://github.com/baidu-netdisk/bdpan-storage --skill bdpan-storage

## 认证

bdpan login — 使用 OAuth 流程在浏览器中授权。令牌存储在 ~/.config/bdpan/config.json。

## Features

- 上传文件到网盘
- 下载网盘文件到本地
- 转存分享链接中的文件
- 创建分享链接
- 列出目录文件
- 登录/注销管理

所有操作限制在 /apps/bdpan/ 目录内。

## 安全

- 不要在公开频道分享认证码
- 共享环境使用后执行 bdpan logout

## Pre-built Scripts

### scripts/bdpan.py
百度网盘 Open API 封装，需设置 BAIDU_NETDISK_TOKEN。

```bash
python3 scripts/bdpan.py ls /apps/
python3 scripts/bdpan.py search "报告"
python3 scripts/bdpan.py info
```
