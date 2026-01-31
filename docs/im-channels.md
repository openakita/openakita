# IM Channel Integration

OpenAkita supports multiple instant messaging platforms.

## Supported Platforms

| Platform | Status | Protocol |
|----------|--------|----------|
| Telegram | ✅ Stable | Bot API |
| DingTalk | ✅ Stable | HTTP Webhook |
| Feishu (Lark) | ✅ Stable | HTTP Webhook |
| WeCom | ✅ Stable | HTTP Webhook |
| QQ | 🧪 Beta | OneBot (WebSocket) |

## Telegram

### Setup

1. **Create a bot** via [@BotFather](https://t.me/botfather):
   ```
   /newbot
   # Follow prompts to create bot
   # Copy the token
   ```

2. **Configure environment**:
   ```bash
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   ```

3. **Run the bot**:
   ```bash
   python scripts/run_telegram_bot.py
   ```

### Features

- Text messages
- Voice messages (transcription)
- Image understanding
- File handling
- Inline keyboards
- Group chat support

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Initialize conversation |
| `/help` | Show help message |
| `/status` | Agent status |
| `/clear` | Clear conversation |
| `/cancel` | Cancel current task |

### Example Usage

```
User: /start
Bot: Hello! I'm OpenAkita. How can I help you?

User: Create a Python script to sort a list
Bot: I'll create that for you...
[Creates and shares the script]
```

## DingTalk

### Setup

1. **Create an application** in [DingTalk Open Platform](https://open.dingtalk.com/)

2. **Get credentials**:
   - App Key
   - App Secret

3. **Configure**:
   ```bash
   DINGTALK_ENABLED=true
   DINGTALK_APP_KEY=your-app-key
   DINGTALK_APP_SECRET=your-app-secret
   ```

4. **Set webhook URL** in DingTalk admin:
   ```
   https://your-domain.com/webhook/dingtalk
   ```

### Features

- Text messages
- Markdown responses
- Action cards
- Group mentions

## Feishu (Lark)

### Setup

1. **Create an app** in [Feishu Open Platform](https://open.feishu.cn/)

2. **Get credentials**:
   - App ID
   - App Secret

3. **Configure**:
   ```bash
   FEISHU_ENABLED=true
   FEISHU_APP_ID=your-app-id
   FEISHU_APP_SECRET=your-app-secret
   ```

4. **Set event URL**:
   ```
   https://your-domain.com/webhook/feishu
   ```

### Features

- Text messages
- Rich text (post)
- Interactive messages
- File sharing

## WeCom (WeChat Work)

### Setup

1. **Create an application** in WeCom admin console

2. **Get credentials**:
   - Corp ID
   - Agent ID
   - Secret

3. **Configure**:
   ```bash
   WEWORK_ENABLED=true
   WEWORK_CORP_ID=your-corp-id
   WEWORK_AGENT_ID=your-agent-id
   WEWORK_SECRET=your-secret
   ```

4. **Set callback URL**:
   ```
   https://your-domain.com/webhook/wework
   ```

### Features

- Text messages
- Markdown
- Image messages
- Mini program cards

## QQ (OneBot)

### Setup

1. **Install OneBot implementation** (e.g., go-cqhttp)

2. **Configure OneBot** to connect via WebSocket

3. **Configure OpenAkita**:
   ```bash
   QQ_ENABLED=true
   QQ_ONEBOT_URL=ws://127.0.0.1:8080
   ```

### Features

- Text messages
- Group messages
- Private messages
- Image handling

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Channel Gateway                          │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Telegram │  │ DingTalk │  │  Feishu  │  │  WeCom   │   │
│  │ Adapter  │  │ Adapter  │  │ Adapter  │  │ Adapter  │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       └─────────────┴───────────┴───────────────┘          │
│                           ↓                                 │
│                  ┌────────────────┐                         │
│                  │ Message Router │                         │
│                  └───────┬────────┘                         │
│                          ↓                                  │
│                  ┌────────────────┐                         │
│                  │ Agent Handler  │                         │
│                  └────────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

## Message Types

### Incoming

| Type | Support |
|------|---------|
| Text | All platforms |
| Image | Telegram, Feishu |
| Voice | Telegram |
| File | Telegram, Feishu |
| Location | Telegram |

### Outgoing

| Type | Support |
|------|---------|
| Text | All platforms |
| Markdown | All platforms |
| Image | All platforms |
| File | Telegram, Feishu |
| Cards | DingTalk, Feishu |

## Deployment

### Single Platform

```bash
# Just Telegram
TELEGRAM_ENABLED=true
python scripts/run_telegram_bot.py
```

### Multiple Platforms

```bash
# All platforms
TELEGRAM_ENABLED=true
DINGTALK_ENABLED=true
FEISHU_ENABLED=true

# Run unified gateway
python -m openakita.channels.gateway
```

### With Reverse Proxy (nginx)

```nginx
server {
    listen 443 ssl;
    server_name bot.example.com;
    
    location /webhook/telegram {
        proxy_pass http://localhost:8001;
    }
    
    location /webhook/dingtalk {
        proxy_pass http://localhost:8002;
    }
    
    location /webhook/feishu {
        proxy_pass http://localhost:8003;
    }
}
```

## Security

### Signature Verification

All webhooks verify signatures:

```python
# DingTalk
signature = hmac.new(
    app_secret.encode(),
    timestamp.encode(),
    hashlib.sha256
).digest()

# Feishu
signature = sha256(timestamp + nonce + encrypt_key + body)
```

### Rate Limiting

Configure per-platform limits:

```bash
TELEGRAM_RATE_LIMIT=30   # messages per minute
DINGTALK_RATE_LIMIT=20
```

## Troubleshooting

### Telegram not responding

1. Check token is correct
2. Verify network can reach `api.telegram.org`
3. Check logs: `LOG_LEVEL=DEBUG python scripts/run_telegram_bot.py`

### Webhook not receiving

1. Verify URL is publicly accessible
2. Check SSL certificate is valid
3. Verify signature verification is correct

### Messages not sending

1. Check API credentials
2. Verify rate limits not exceeded
3. Check message format is correct for platform
