# 多端访问指南

## 概述

OpenAkita 支持多种方式访问。根据你的网络环境选择合适的方案：

| 场景 | 难度 | 需要配置 |
|------|------|---------|
| 本地访问 | ⭐ | 无 |
| 局域网访问 | ⭐⭐ | 开启外部访问开关 + 密码 |
| 公网访问 | ⭐⭐⭐⭐ | 云服务器 + 反向代理 |
| IM 机器人 | ⭐⭐ | 仅配置 IM 通道 |

## 场景 1：本地访问（默认）

开箱即用，无需额外配置。

- **地址**：`http://127.0.0.1:18900`
- **认证**：无需密码（仅本机可访问）
- **适用**：在运行 OpenAkita 的电脑上使用

启动后打开浏览器访问即可，或使用桌面应用（Tauri）。

## 场景 2：局域网访问

让同一 WiFi / 路由器下的其他设备（手机、平板、其他电脑）也能使用。

### 步骤 1：开启外部访问

在 [高级配置](/web/#/config/advanced) 中找到 **网络与安全 → Web 访问 / 网络**，打开「**允许外部访问（局域网/公网）**」开关，然后点击「应用并重启」。

### 步骤 2：查看你的局域网 IP

```bash
# Windows
ipconfig
# 找到 "IPv4 地址": 192.168.x.x

# Linux / Mac
ifconfig
# 或 ip addr
```

### 步骤 3：设置 Web 访问密码

::: warning 重要
开放局域网访问后，同一网络内的任何人都能访问。务必在 [高级配置](/web/#/config/advanced) 中设置 Web 访问密码。
:::

### 步骤 4：其他设备访问

在手机或平板浏览器中打开：

```
http://192.168.x.x:18900
```

将 `x.x` 替换为你在步骤 2 中看到的实际 IP。输入密码后即可使用。

### 检查防火墙

如果访问不了，可能是防火墙拦截了端口。参考 [网络基础科普](/network/basics) 中的防火墙放行方法。

## 场景 3：公网 / 远程访问

从任意网络位置访问你的 OpenAkita 实例。

### 方案 A：云服务器部署

1. 在云服务器（阿里云 / 腾讯云 / AWS 等）上部署 OpenAkita
2. 参考 [生产部署](/network/production) 指南配置 Docker Compose
3. 配置反向代理（Nginx 或 Caddy）实现 HTTPS

### 方案 B：Caddy 反向代理（推荐）

Caddy 自动申请 HTTPS 证书，配置最简单：

```
ai.your-domain.com {
    reverse_proxy localhost:18900
}
```

### 方案 C：Nginx 反向代理

```nginx
server {
    listen 443 ssl;
    server_name ai.your-domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:18900;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### 配置 OpenAkita

在 [高级配置](/web/#/config/advanced) 的 **网络与安全** 中设置：

1. 打开「**允许外部访问（局域网/公网）**」开关
2. 打开「**反向代理模式（Nginx/Caddy）**」开关
3. 设置 **Web 访问密码**（公网必须设置）
4. 点击「应用并重启」

## 场景 4：通过 IM 机器人访问

**最简单的远程访问方案**——无需配置网络，平台替你处理路由。

| IM 平台 | 特点 |
|---------|------|
| **Telegram** | 全球可用，配置最简单，推荐首选 |
| **飞书** | 企业用户友好，支持流式卡片 |
| **钉钉** | Stream 协议无需公网 IP |
| **企业微信** | WebSocket 模式同样无需公网 |

工作原理：IM 平台（Telegram 服务器、飞书服务器等）负责消息的收发，你的 OpenAkita 实例通过 **长连接或轮询** 与平台通信，无需暴露端口。

配置方法见 [消息通道（IM）](/features/im-channels)。

::: tip 最佳实践
IM 机器人 + 本地部署 = 最安全的远程访问方案。Agent 运行在你自己的电脑上，只通过 IM 平台的加密通道通信，不暴露任何端口。
:::

## 认证机制

| 访问方式 | 认证方式 | 说明 |
|---------|---------|------|
| 本地（127.0.0.1） | 无需认证 | 默认信任本机 |
| 局域网 | Web 密码 | 在高级配置中设置 |
| 公网 | Web 密码 + HTTPS | 必须配合 HTTPS 使用 |
| API 调用 | JWT / API Key | 用于程序化访问 |
| IM 机器人 | 配对码 | 首次连接时一次性验证 |

## 访问方式总览

| 方式 | 平台 | 网络要求 | 适用场景 |
|------|------|---------|---------|
| 桌面应用 | Windows / Mac / Linux | 本地 | 日常主力使用 |
| Web 浏览器 | 任意设备 | 本地 / LAN / 公网 | 灵活访问 |
| 手机浏览器 | iOS / Android | LAN / 公网 | 移动端使用 |
| IM 机器人 | Telegram / 飞书 / 钉钉等 | 仅需互联网 | 随时随地 |

## 相关页面

- [网络基础科普](/network/basics) — IP、端口、防火墙等概念解释
- [生产部署](/network/production) — Docker Compose 部署方案
- [高级设置](/advanced/advanced) — 网络与安全配置
- [消息通道（IM）](/features/im-channels) — IM 平台接入教程
