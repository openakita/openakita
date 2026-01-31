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
- **Voice messages** (automatic transcription via local Whisper)
- **Image understanding** (multimodal input to LLM)
- File handling
- Inline keyboards
- Group chat support
- **Pairing security** (prevents unauthorized access)

### Voice Message Processing

Voice messages are automatically transcribed using local Whisper:

```
User: [sends voice message]
System: Downloads → Whisper transcription → Text sent to LLM
Agent: Responds based on transcribed text
```

Configuration:
- Model: `base` (default, good balance of speed/quality)
- Language: Chinese (default)
- No API calls needed, fully offline

### Pairing Security

First-time users must pair with a security code:

1. Bot generates a random pairing code
2. Code is saved to `data/pairing/telegram_pairing_code.txt`
3. User sends the code to the bot
4. Paired users are saved to `data/pairing/telegram_pairs.json`

```
User: /start
Bot: Please enter pairing code (check: data/pairing/telegram_pairing_code.txt)
User: ABC123
Bot: ✅ Pairing successful!
```

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

| Type | Support | Processing |
|------|---------|------------|
| Text | All platforms | Direct to LLM |
| Image | Telegram, Feishu | Base64 encoded, multimodal input |
| Voice | Telegram | Auto-transcribed via Whisper |
| File | Telegram, Feishu | Downloaded to local storage |
| Location | Telegram | Coordinates extracted |

### Outgoing

| Type | Support | Tool |
|------|---------|------|
| Text | All platforms | `send_to_chat` |
| Markdown | All platforms | `send_to_chat` |
| Image | All platforms | `send_to_chat` with `file_path` |
| Voice | Telegram | `send_to_chat` with `voice_path` |
| File | Telegram, Feishu | `send_to_chat` with `file_path` |
| Cards | DingTalk, Feishu | Platform-specific |

## LLM Tools for IM

The Agent has access to these IM-specific tools:

| Tool | Description |
|------|-------------|
| `send_to_chat` | Send text, image, voice, or file to user |
| `get_chat_history` | Query recent chat messages (user, assistant, system) |
| `get_voice_file` | Get local path of user's voice message |
| `get_image_file` | Get local path of user's image |

### Example: Chat History

```
User: Show me the last 5 messages
Agent: [calls get_chat_history with limit=5]
Agent: Here are the recent messages:
       1. 👤 User (10:30): Hello
       2. 🤖 Assistant (10:30): Hi! How can I help?
       3. ⚙️ System (10:31): Task completed: Stock monitoring
       ...
```

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
