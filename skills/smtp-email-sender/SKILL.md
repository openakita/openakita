---
name: smtp-email-sender
description: Send emails via SMTP (Gmail, Outlook, etc.). Supports attachments, HTML content, and multiple recipients. Use when user asks to send email, compose email, or email someone.
---

# SMTP Email Sender

通过 SMTP 协议发送邮件，支持 Gmail、Outlook、企业邮箱等。

## 前置要求

### 1. Gmail 用户

如果使用 Gmail，需要：
1. 启用两步验证
2. 创建应用专用密码（App Password）
   - 访问：https://myaccount.google.com/apppasswords
   - 选择"邮件"和应用名称
   - 复制生成的 16 位密码

### 2. Outlook/Hotmail 用户

1. 启用两步验证
2. 创建应用密码：https://account.microsoft.com/security
3. 或使用普通密码（如果允许）

### 3. 企业邮箱用户

联系 IT 部门获取：
- SMTP 服务器地址
- SMTP 端口（通常 587 或 465）
- 是否需要 SSL/TLS

## 配置

在 `.env` 文件中添加以下环境变量：

```bash
# SMTP 配置
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password  # Gmail 使用应用专用密码
SMTP_USE_TLS=true
```

或者首次使用时运行配置脚本。

## 使用方法

### 基本用法

调用 `send_email.py` 脚本：

```bash
python scripts/send_email.py \
  --to recipient@example.com \
  --subject "邮件主题" \
  --body "邮件正文"
```

### 完整参数

| 参数 | 必需 | 说明 |
|------|------|------|
| `--to` | 是 | 收件人邮箱（多个用逗号分隔） |
| `--subject` | 是 | 邮件主题 |
| `--body` | 是 | 邮件正文 |
| `--cc` | 否 | 抄送邮箱（多个用逗号分隔） |
| `--bcc` | 否 | 密送邮箱（多个用逗号分隔） |
| `--attachment` | 否 | 附件路径（多个用逗号分隔） |
| `--is_html` | 否 | 正文是否为 HTML 格式（默认 false） |
| `--from_name` | 否 | 发件人显示名称 |

### 示例

**发送简单邮件**：
```bash
python scripts/send_email.py \
  --to friend@example.com \
  --subject "周末聚会" \
  --body "这周末有空吗？一起吃饭吧！"
```

**发送 HTML 邮件带附件**：
```bash
python scripts/send_email.py \
  --to boss@company.com \
  --subject "项目报告" \
  --body "<h1>项目进度报告</h1><p>详见附件...</p>" \
  --is_html true \
  --attachment "report.pdf,chart.xlsx" \
  --from_name "张三"
```

**发送给多人**：
```bash
python scripts/send_email.py \
  --to "alice@example.com,bob@example.com" \
  --cc "manager@example.com" \
  --subject "会议纪要" \
  --body "今天的会议纪要如下..."
```

## 支持的 SMTP 配置

### Gmail
```
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### Outlook/Hotmail
```
SMTP_SERVER=smtp-mail.outlook.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### QQ 邮箱
```
SMTP_SERVER=smtp.qq.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### 163 邮箱
```
SMTP_SERVER=smtp.163.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### 企业邮箱（示例）
```
SMTP_SERVER=smtp.company.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

## 常见问题

### 1. 认证失败

**Gmail**：
- 确保启用了两步验证
- 使用应用专用密码，不是普通密码
- 检查是否开启了"不够安全的应用"访问（不推荐）

**Outlook**：
- 检查是否需要应用密码
- 确认 SMTP 地址正确

### 2. 连接超时

- 检查防火墙设置
- 尝试端口 465（SSL）代替 587（TLS）
- 确认 SMTP 服务器地址正确

### 3. 附件太大

- Gmail 限制 25MB
- Outlook 限制 20MB
- 大文件建议使用云盘链接

## 安全建议

1. **永远不要**在代码中硬编码密码
2. 使用环境变量或加密的配置文件
3. 定期更换应用专用密码
4. 不要在公共网络使用 SMTP 发送敏感信息

## 故障排除

运行测试脚本验证配置：

```bash
python scripts/test_smtp.py
```

如果测试失败，检查：
1. `.env` 文件配置是否正确
2. 网络连接是否正常
3. 邮箱账号密码是否正确
4. 防火墙是否阻止 SMTP 端口
