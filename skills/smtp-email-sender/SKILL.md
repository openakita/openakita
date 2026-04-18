---
name: smtp-email-sender
description: Send emails via SMTP (Gmail, Outlook, etc.). Supports attachments, HTML content, and multiple recipients. Use when user asks to send email, compose email, or email someone.
---

# SMTP Email Sender

Via SMTP 协议Send邮件，Supports Gmail、Outlook、企业邮箱等。

## 前置要求

### 1. Gmail 用户

如果Use Gmail，需要：
1. Enable两步验证
2. Create应用专用密码（App Password）
   - 访问：https://myaccount.google.com/apppasswords
   - 选择"邮件"和应用名称
   - 复制Generation的 16 位密码

### 2. Outlook/Hotmail 用户

1. Enable两步验证
2. Create应用密码：https://account.microsoft.com/security
3. 或Use普通密码（如果Allows）

### 3. 企业邮箱用户

联系 IT 部门Get：
- SMTP 服务器地址
- SMTP 端口（通常 587 或 465）
- YesNo需要 SSL/TLS

## Configuration

在 `.env` 文件中添加以下环境变量：

```bash
# SMTP 配置
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password  # Gmail Use应用专用密码
SMTP_USE_TLS=true
```

或者首次Use时Run配置脚本。

## Usage

### 基本用法

Call `send_email.py` 脚本：

```bash
python scripts/send_email.py \
  --to recipient@example.com \
  --subject "邮件主题" \
  --body "邮件正文"
```

### Full参数

| Parameter | 必需 | Description |
|------|------|------|
| `--to` | Yes | 收件人邮箱（多个用逗号分隔） |
| `--subject` | Yes | 邮件主题 |
| `--body` | Yes | 邮件正文 |
| `--cc` | No | 抄送邮箱（多个用逗号分隔） |
| `--bcc` | No | 密送邮箱（多个用逗号分隔） |
| `--attachment` | No | 附件路径（多个用逗号分隔） |
| `--is_html` | No | 正文YesNo为 HTML 格式（Default false） |
| `--from_name` | No | 发件人Display名称 |

### Examples

**Send简单邮件**：
```bash
python scripts/send_email.py \
  --to friend@example.com \
  --subject "周末聚会" \
  --body "这周末有空吗？一起吃饭吧！"
```

**Send HTML 邮件带附件**：
```bash
python scripts/send_email.py \
  --to boss@company.com \
  --subject "项目报告" \
  --body "<h1>项目进度报告</h1><p>详见附件...</p>" \
  --is_html true \
  --attachment "report.pdf,chart.xlsx" \
  --from_name "张三"
```

**Send给多人**：
```bash
python scripts/send_email.py \
  --to "alice@example.com,bob@example.com" \
  --cc "manager@example.com" \
  --subject "会议纪要" \
  --body "今天的会议纪要如下..."
```

## Supports的 SMTP 配置

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

## FAQ

### 1. 认证失败

**Gmail**：
- 确保Enable了两步验证
- Use应用专用密码，不Yes普通密码
- 检查YesNo开启了"不够安全的应用"访问（不Recommendations）

**Outlook**：
- 检查YesNo需要应用密码
- 确认 SMTP 地址正确

### 2. 连接超时

- 检查防火墙Set
- 尝试端口 465（SSL）代替 587（TLS）
- 确认 SMTP 服务器地址正确

### 3. 附件太大

- Gmail 限制 25MB
- Outlook 限制 20MB
- 大文件建议Use云盘链接

## 安全建议

1. **永远不要**在代码中硬编码密码
2. Use环境变量或加密的配置文件
3. 定期更换应用专用密码
4. 不要在公共网络Use SMTP Send敏感信息

## 故障排除

Run测试脚本验证配置：

```bash
python scripts/test_smtp.py
```

如果测试失败，检查：
1. `.env` 文件配置YesNo正确
2. 网络连接YesNo正常
3. 邮箱账号密码YesNo正确
4. 防火墙YesNo阻止 SMTP 端口
