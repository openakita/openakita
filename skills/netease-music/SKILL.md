---
name: openakita/skills@netease-music
description: "NetEase Cloud Music skill for searching songs, managing playlists, getting personalized recommendations, and controlling playback via ncm-cli. Use when user wants to search music, play songs, manage playlists, or get music recommendations."
license: MIT
metadata:
  author: NetEase
  version: "1.0.0"
---

# NetEase Music

通过 ncm-cli 控制网易云音乐，支持搜索、播放、歌单管理和智能推荐。

## Installation

npm install -g @music163/ncm-cli
ncm-cli configure

按向导输入 App ID 和 Private Key（需在 https://developer.music.163.com 入驻获取）。

## 登录

ncm-cli login — 使用网易云音乐 App 扫码授权。

## 三层技能架构

### ncm-cli-setup
安装配置 ncm-cli 工具。

### netease-music-cli
基础操作：搜索歌曲/歌单/专辑、播放控制、歌单管理、获取每日推荐。

### netease-music-assistant
智能推荐：基于红心歌曲分析偏好，自动搜索并推荐个性化音乐。

## 使用示例

搜索歌曲、播放音乐、创建歌单、获取推荐等，均可用自然语言描述。

## Pre-built Scripts

### scripts/setup.py
网易云音乐 ncm-cli 安装配置脚本。

```bash
python3 scripts/setup.py
```

### scripts/music_quick.py
网易云音乐快捷操作脚本。

```bash
python3 scripts/music_quick.py search --keyword "周杰伦"
python3 scripts/music_quick.py playlist --id 123456
python3 scripts/music_quick.py recommend
python3 scripts/music_quick.py play --id 789
```
