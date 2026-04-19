---
name: smtp-email-sender
description: Send emails via SMTP (Gmail, Outlook, etc.). Supports attachments, HTML content, and multiple recipients. Use when user asks to send email, compose email, or email someone.
---

# SMTP Email Sender

Via SMTP Send, Supports Gmail, Outlook,. 

## need

### 1. Gmail

Use Gmail, need: 
1. Enable
2. Create (App Password) 
-: https://myaccount.google.com/apppasswords
- ""and
- Generation 16

### 2. Outlook/Hotmail

1. Enable
2. Create: https://account.microsoft.com/security
3. orUse (Allows) 

### 3.

IT Get: 
- SMTP
- SMTP ( 587 or 465) 
- YesNoneed SSL/TLS

## Configuration

in `.env`: 

```bash
# SMTP
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password # Gmail Use
SMTP_USE_TLS=true
```

orUseRun. 

## Usage

###

Call `send_email.py`: 

```bash
python scripts/send_email.py \
 --to recipient@example.com \
--subject "" \
--body ""
```

### Full

| Parameter | | Description |
|------|------|------|
| `--to` | Yes | () |
| `--subject` | Yes | |
| `--body` | Yes | |
| `--cc` | No | () |
| `--bcc` | No | () |
| `--attachment` | No | () |
| `--is_html` | No | YesNo HTML (Default false) |
| `--from_name` | No | Display |

### Examples

**Send**: 
```bash
python scripts/send_email.py \
 --to friend@example.com \
--subject "will" \
--body "thishave?! "
```

**Send HTML **: 
```bash
python scripts/send_email.py \
 --to boss@company.com \
--subject "" \
--body "<h1></h1><p>...</p>" \
 --is_html true \
 --attachment "report.pdf,chart.xlsx" \
--from_name ""
```

**Send**: 
```bash
python scripts/send_email.py \
 --to "alice@example.com,bob@example.com" \
 --cc "manager@example.com" \
--subject "willneed" \
--body " willneed..."
```

## Supports SMTP

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

### QQ
```
SMTP_SERVER=smtp.qq.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### 163
```
SMTP_SERVER=smtp.163.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### Enterprise () 
```
SMTP_SERVER=smtp.company.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

## FAQ

### 1.

**Gmail**: 
- Enable
- Use, notYes
- YesNo "not " (notRecommendations) 

**Outlook**: 
- YesNoneed
- SMTP

### 2.

- Set
- 465 (SSL) 587 (TLS) 
- SMTP

### 3.

- Gmail 25MB
- Outlook 20MB
- Use

## Secure

1. **notneed**in
2. Useor
3.
4. notneedinUse SMTP Send

## Troubleshooting

Run: 

```bash
python scripts/test_smtp.py
```

,: 
1. `.env` YesNo
2. YesNo
3. YesNo
4. YesNo SMTP