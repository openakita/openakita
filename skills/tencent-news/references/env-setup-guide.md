# TENCENT_NEWS_APIKEY 配置指南

> 本指南面向**用户**，用于手动获取和配置 API Key。

## 获取 API Key

1. 打开浏览器访问 [API Key 获取页面](https://news.qq.com/exchange?scene=appkey)
2. 按页面引导完成获取

## 设置 API Key

打开终端（macOS / Linux）或 PowerShell（Windows），执行：

```sh
tencent-news-cli apikey-set YOUR_KEY
```

> `YOUR_KEY` 替换为实际获取到的 Key 值，不需要加引号。

验证：

```sh
tencent-news-cli apikey-get
```

## 清除 API Key

仅在需要重置时执行：

```sh
tencent-news-cli apikey-clear
```

## 常见问题

- **`API Key 无效`** → 重新前往获取页面生成新 Key
- **`operation not permitted`** → 确认在有写入权限的终端中执行命令
- **找不到 `tencent-news-cli` 命令** → 重新打开终端，或参考 [安装指南](installation-guide.md) 重新安装
