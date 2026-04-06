# tencent-news-cli 手动安装指南

> 本指南面向**用户**。通常 AI 助手会通过技能脚本自动完成安装，只有在脚本安装失败时才需要参考此指南手动操作。

## macOS / Linux

打开终端，执行以下命令：

```sh
curl -fsSL https://mat1.gtimg.com/qqcdn/qqnews/cli/hub/tencent-news/setup.sh | sh
```

脚本会自动完成：识别系统和架构 → 下载 CLI → 验证 → 配置环境变量 → 检测 API Key 状态。

安装完成后重新打开终端（或执行 `source ~/.zshrc`），运行 `tencent-news-cli help` 确认安装成功。

## Windows

打开 PowerShell，执行以下命令：

```powershell
irm https://mat1.gtimg.com/qqcdn/qqnews/cli/hub/tencent-news/setup.ps1 | iex
```

安装完成后重新打开 PowerShell，运行 `tencent-news-cli help` 确认安装成功。

## 故障排查

- **macOS 安全提示**（"无法打开" / "未验证的开发者"）→ 前往「系统设置 → 隐私与安全性」，点击「仍要打开」
- **Windows SmartScreen 拦截** → 在系统提示中选择「更多信息」后允许运行
- **下载失败** → 检查网络连接，确认 CDN 地址 `mat1.gtimg.com` 可达
- **`unsupported os` 或 `unsupported architecture`** → 当前平台不在支持范围内
