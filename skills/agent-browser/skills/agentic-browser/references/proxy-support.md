# Proxy Support

Proxy configuration for geo-testing, privacy, and corporate environments.

**Related**: [commands.md](commands.md) for full function reference, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [Basic Proxy Configuration](#basic-proxy-configuration)
- [Authenticated Proxy](#authenticated-proxy)
- [Common Use Cases](#common-use-cases)
- [Proxy Types](#proxy-types)
- [Verifying Proxy Connection](#verifying-proxy-connection)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

## Basic Proxy Configuration

Set proxy when opening a session:

```bash
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://example.com",
  "proxy_url": "http://proxy.example.com:8080"
}' | jq -r '.session_id')
```

All traffic for this session routes through the proxy.

## Authenticated Proxy

For proxies requiring username/password:

```bash
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://example.com",
  "proxy_url": "http://proxy.example.com:8080",
  "proxy_username": "myuser",
  "proxy_password": "mypassword"
}' | jq -r '.session_id')
```

## Common Use Cases

### Geo-Location Testing

Test how your site appears from different regions:

```bash
#!/bin/bash
# Test from multiple regions

PROXIES=(
  "us|http://us-proxy.example.com:8080"
  "eu|http://eu-proxy.example.com:8080"
  "asia|http://asia-proxy.example.com:8080"
)

for entry in "${PROXIES[@]}"; do
  REGION="${entry%%|*}"
  PROXY="${entry##*|}"

  echo "Testing from: $REGION"

  SESSION=$(infsh app run agent-browser --function open --session new --input '{
    "url": "https://mysite.com",
    "proxy_url": "'"$PROXY"'"
  }' | jq -r '.session_id')

  # Take screenshot
  infsh app run agent-browser --function screenshot --session $SESSION --input '{
    "full_page": true
  }' > "${REGION}-screenshot.json"

  # Get page content
  RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION --input '{}')
  echo $RESULT | jq '.elements_text' > "${REGION}-elements.txt"

  infsh app run agent-browser --function close --session $SESSION --input '{}'
done

echo "Geo-testing complete"
```

### Rate Limit Avoidance

Rotate proxies for web scraping:

```bash
#!/bin/bash
# Rotate through proxy list

PROXIES=(
  "http://proxy1.example.com:8080"
  "http://proxy2.example.com:8080"
  "http://proxy3.example.com:8080"
)

URLS=(
  "https://site.com/page1"
  "https://site.com/page2"
  "https://site.com/page3"
)

for i in "${!URLS[@]}"; do
  # Rotate proxy
  PROXY_INDEX=$((i % ${#PROXIES[@]}))
  PROXY="${PROXIES[$PROXY_INDEX]}"
  URL="${URLS[$i]}"

  echo "Fetching $URL via proxy $((PROXY_INDEX + 1))"

  SESSION=$(infsh app run agent-browser --function open --session new --input '{
    "url": "'"$URL"'",
    "proxy_url": "'"$PROXY"'"
  }' | jq -r '.session_id')

  # Extract data
  RESULT=$(infsh app run agent-browser --function execute --session $SESSION --input '{
    "code": "document.body.innerText"
  }')
  echo $RESULT | jq -r '.result' > "page-$i.txt"

  infsh app run agent-browser --function close --session $SESSION --input '{}'

  # Polite delay
  sleep 1
done
```

### Corporate Network Access

Access sites through corporate proxy:

```bash
# Use corporate proxy for external sites
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://external-vendor.com",
  "proxy_url": "http://corpproxy.company.com:8080",
  "proxy_username": "'"$CORP_USER"'",
  "proxy_password": "'"$CORP_PASS"'"
}' | jq -r '.session_id')
```

### Privacy and Anonymity

Route through privacy-focused proxy:

```bash
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://whatismyip.com",
  "proxy_url": "socks5://privacy-proxy.example.com:1080"
}' | jq -r '.session_id')
```

## Proxy Types

### HTTP/HTTPS Proxy

```json
{"proxy_url": "http://proxy.example.com:8080"}
{"proxy_url": "https://proxy.example.com:8080"}
```

### SOCKS5 Proxy

```json
{"proxy_url": "socks5://proxy.example.com:1080"}
```

### With Authentication

```json
{
  "proxy_url": "http://proxy.example.com:8080",
  "proxy_username": "user",
  "proxy_password": "pass"
}
```

## Verifying Proxy Connection

Check that traffic routes through proxy:

```bash
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://httpbin.org/ip",
  "proxy_url": "http://proxy.example.com:8080"
}' | jq -r '.session_id')

# Get the IP shown
RESULT=$(infsh app run agent-browser --function execute --session $SESSION --input '{
  "code": "document.body.innerText"
}')
echo "IP via proxy: $(echo $RESULT | jq -r '.result')"

infsh app run agent-browser --function close --session $SESSION --input '{}'
```

The IP should be the proxy's IP, not your real IP.

## Troubleshooting

### Connection Failed

```
Error: Failed to open URL: net::ERR_PROXY_CONNECTION_FAILED
```

**Solutions:**
1. Verify proxy URL is correct
2. Check proxy is running and accessible
3. Confirm port is correct
4. Test proxy with curl: `curl -x http://proxy:8080 https://example.com`

### Authentication Failed

```
Error: 407 Proxy Authentication Required
```

**Solutions:**
1. Verify username/password are correct
2. Check if proxy requires different auth method
3. Ensure credentials don't contain special characters that need escaping

### SSL Errors

Some proxies perform SSL inspection. If you see certificate errors:

```bash
# The browser should handle most SSL proxies automatically
# If issues persist, verify proxy SSL certificate is valid
```

### Slow Performance

**Solutions:**
1. Choose proxy closer to target site
2. Use faster proxy provider
3. Reduce number of requests per session

## Best Practices

### 1. Use Environment Variables

```bash
# Good: Credentials in env vars
'{"proxy_url": "'"$PROXY_URL"'", "proxy_username": "'"$PROXY_USER"'"}'

# Bad: Hardcoded
'{"proxy_url": "http://user:pass@proxy.com:8080"}'
```

### 2. Test Proxy Before Automation

```bash
# Verify proxy works
curl -x "$PROXY_URL" https://httpbin.org/ip
```

### 3. Handle Proxy Failures

```bash
# Retry with different proxy on failure
for PROXY in "${PROXIES[@]}"; do
  SESSION=$(infsh app run agent-browser --function open --session new --input '{
    "url": "'"$URL"'",
    "proxy_url": "'"$PROXY"'"
  }' 2>&1)

  if echo "$SESSION" | jq -e '.session_id' > /dev/null 2>&1; then
    SESSION_ID=$(echo $SESSION | jq -r '.session_id')
    break
  fi
  echo "Proxy $PROXY failed, trying next..."
done
```

### 4. Respect Rate Limits

Even with proxies, be a good citizen:

```bash
# Add delays between requests
'{"action": "wait", "wait_ms": 1000}'
```

### 5. Log Proxy Usage

For debugging, log which proxy was used:

```bash
echo "$(date): Using proxy $PROXY for $URL" >> proxy.log
```
