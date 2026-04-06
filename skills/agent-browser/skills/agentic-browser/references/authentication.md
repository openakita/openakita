# Authentication Patterns

Login flows, OAuth, 2FA, and authenticated browsing.

**Related**: [session-management.md](session-management.md) for session details, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [Basic Login Flow](#basic-login-flow)
- [OAuth / SSO Flows](#oauth--sso-flows)
- [Two-Factor Authentication](#two-factor-authentication)
- [Session Reuse Patterns](#session-reuse-patterns)
- [Cookie Extraction](#cookie-extraction)
- [Security Best Practices](#security-best-practices)

## Basic Login Flow

Standard username/password login:

```bash
#!/bin/bash

# Start session
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://app.example.com/login"
}' | jq -r '.session_id')

# Get form elements
# Expected: @e1 [input type="email"], @e2 [input type="password"], @e3 [button] "Sign In"

# Fill credentials
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "fill", "ref": "@e1", "text": "user@example.com"
}'

infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "fill", "ref": "@e2", "text": "'"$PASSWORD"'"
}'

# Submit
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e3"
}'

# Wait for redirect
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 2000
}'

# Verify login succeeded
RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION --input '{}')
URL=$(echo $RESULT | jq -r '.url')

if [[ "$URL" == *"/login"* ]]; then
  echo "Login failed - still on login page"
  exit 1
fi

echo "Login successful"
# Continue with authenticated actions...
```

## OAuth / SSO Flows

For OAuth redirects (Google, GitHub, etc.):

```bash
#!/bin/bash

SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://app.example.com/auth/google"
}' | jq -r '.session_id')

# Wait for redirect to Google
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 3000
}'

# Snapshot to see Google login form
RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION --input '{}')
echo $RESULT | jq '.elements_text'

# Fill Google email
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "fill", "ref": "@e1", "text": "user@gmail.com"
}'

# Click Next
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e2"
}'

# Wait and snapshot for password field
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 2000
}'
RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION --input '{}')

# Fill password
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "fill", "ref": "@e1", "text": "'"$GOOGLE_PASSWORD"'"
}'

# Click Sign in
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e2"
}'

# Wait for redirect back to app
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 5000
}'

# Verify we're back on the app
RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION --input '{}')
URL=$(echo $RESULT | jq -r '.url')
echo "Final URL: $URL"
```

## Two-Factor Authentication

For 2FA, you may need human intervention or TOTP generation:

### With TOTP Code

```bash
# After password, check for 2FA prompt
RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION --input '{}')
ELEMENTS=$(echo $RESULT | jq -r '.elements_text')

if echo "$ELEMENTS" | grep -qi "verification\|2fa\|authenticator"; then
  # Generate TOTP code (requires oathtool)
  TOTP_CODE=$(oathtool --totp -b "$TOTP_SECRET")

  # Fill 2FA code
  infsh app run agent-browser --function interact --session $SESSION --input '{
    "action": "fill", "ref": "@e1", "text": "'"$TOTP_CODE"'"
  }'

  # Submit
  infsh app run agent-browser --function interact --session $SESSION --input '{
    "action": "click", "ref": "@e2"
  }'
fi
```

### With Manual Intervention

For SMS or hardware token 2FA:

```bash
# Record video so user can see the 2FA prompt
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://app.example.com/login",
  "record_video": true
}' | jq -r '.session_id')

# ... login flow ...

# At 2FA step, prompt user
echo "2FA code sent. Enter the code:"
read -r CODE

infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "fill", "ref": "@e1", "text": "'"$CODE"'"
}'
```

## Session Reuse Patterns

Since sessions maintain cookies, you can reuse authenticated sessions:

```bash
#!/bin/bash
# login-and-work.sh

# Login once
login() {
  SESSION=$(infsh app run agent-browser --function open --session new --input '{
    "url": "https://app.example.com/login"
  }' | jq -r '.session_id')

  # ... login steps ...

  echo $SESSION
}

# Do work with authenticated session
do_work() {
  local SESSION=$1

  # Navigate to protected page
  infsh app run agent-browser --function interact --session $SESSION --input '{
    "action": "goto", "url": "https://app.example.com/dashboard"
  }'

  # Extract data
  infsh app run agent-browser --function snapshot --session $SESSION --input '{}'
}

# Main
SESSION=$(login)
do_work $SESSION

# Don't close if you want to reuse!
# infsh app run agent-browser --function close --session $SESSION --input '{}'
```

## Cookie Extraction

Extract cookies for use in other tools:

```bash
# Get cookies via JavaScript
RESULT=$(infsh app run agent-browser --function execute --session $SESSION --input '{
  "code": "document.cookie"
}')
COOKIES=$(echo $RESULT | jq -r '.result')
echo "Cookies: $COOKIES"

# Get all cookies including httpOnly (more complete)
RESULT=$(infsh app run agent-browser --function execute --session $SESSION --input '{
  "code": "JSON.stringify(performance.getEntriesByType(\"resource\").map(r => r.name))"
}')
```

## Security Best Practices

### 1. Never Hardcode Credentials

```bash
# Good: Use environment variables
'{"action": "fill", "ref": "@e2", "text": "'"$PASSWORD"'"}'

# Bad: Hardcoded
'{"action": "fill", "ref": "@e2", "text": "mypassword123"}'
```

### 2. Use Secure Environment Variables

```bash
# Set securely
export PASSWORD=$(cat /path/to/secure/password)

# Or use a secrets manager
export PASSWORD=$(vault read -field=password secret/app)
```

### 3. Don't Log Sensitive Data

```bash
# Good: Redact sensitive info
echo "Logging in as $USERNAME"

# Bad: Logging passwords
echo "Password: $PASSWORD"  # Never do this!
```

### 4. Close Sessions After Use

```bash
# Always clean up
trap 'infsh app run agent-browser --function close --session $SESSION --input "{}" 2>/dev/null' EXIT
```

### 5. Use Video Recording for Debugging Only

Video may capture sensitive information:

```bash
# Only enable when debugging
if [ "$DEBUG" = "true" ]; then
  RECORD_VIDEO="true"
else
  RECORD_VIDEO="false"
fi
```

### 6. Verify Login Success

Always confirm authentication worked:

```bash
# Check URL changed from login page
URL=$(echo $RESULT | jq -r '.url')
if [[ "$URL" == *"/login"* ]] || [[ "$URL" == *"/signin"* ]]; then
  echo "ERROR: Login failed"
  exit 1
fi

# Or check for specific element on authenticated page
ELEMENTS=$(echo $RESULT | jq -r '.elements_text')
if ! echo "$ELEMENTS" | grep -q "Logout\|Dashboard\|Welcome"; then
  echo "ERROR: Not authenticated"
  exit 1
fi
```
