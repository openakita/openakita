---
name: openakita/skills@baidu-netdisk
description: "Baidu Netdisk (Baidu Cloud) file management skill. Upload, download, transfer, share, and list files. Use when user wants to manage files on Baidu Netdisk cloud storage."
license: MIT
metadata:
  author: baidu-netdisk
  version: "1.0.0"
---

# Baidu NetDisk

A dedicated cloud digital assistant for individuals and enterprises — file upload/download, backup, sharing, and management, all in one command.

## Installation

npx skills add https://github.com/baidu-netdisk/bdpan-storage --skill bdpan-storage

## Authentication

bdpan login — Use the OAuth flow to authorize in a browser. Tokens are stored in ~/.config/bdpan/config.json.

## Features

- Upload files to NetDisk
- Download NetDisk files to local machine
- Save files from shared links
- Create share links
- List directory contents
- Login/logout management

All operations are restricted to the /apps/bdpan/ directory.

## Security

- Do not share authentication codes in public channels
- After using shared environments, run bdpan logout

## Pre-built Scripts

### scripts/bdpan.py
Baidu NetDisk Open API wrapper. Requires BAIDU_NETDISK_TOKEN environment variable.

```bash
python3 scripts/bdpan.py ls /apps/
python3 scripts/bdpan.py search "report"
python3 scripts/bdpan.py info
```
