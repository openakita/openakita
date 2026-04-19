---
name: openakita/skills@netease-music
description: "NetEase Cloud Music skill for searching songs, managing playlists, getting personalized recommendations, and controlling playback via ncm-cli. Use when user wants to search music, play songs, manage playlists, or get music recommendations."
license: MIT
metadata:
 author: NetEase
 version: "1.0.0"
---

# NetEase Music

Via ncm-cli, SupportsSearch,, ManageandRecommendations. 

## Installation

npm install -g @music163/ncm-cli
ncm-cli configure

App ID and Private Key (in https://developer.music.163.com Get). 

##

ncm-cli login — Use App. 

##

### ncm-cli-setup
ncm-cli. 

### netease-music-cli
: Search//,, Manage, GetRecommendations. 

### netease-music-assistant
Recommendations: Based onAnalyze, AutomaticSearchRecommendations. 

## Usage Examples

Search,, Create, GetRecommendations,. 

## Pre-built Scripts

### scripts/setup.py
ncm-cli. 

```bash
python3 scripts/setup.py
```

### scripts/music_quick.py
. 

```bash
python3 scripts/music_quick.py search --keyword ""
python3 scripts/music_quick.py playlist --id 123456
python3 scripts/music_quick.py recommend
python3 scripts/music_quick.py play --id 789
```