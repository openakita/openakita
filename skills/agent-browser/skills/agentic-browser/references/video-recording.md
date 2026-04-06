# Video Recording

Capture browser automation as video for debugging, documentation, or verification.

**Related**: [commands.md](commands.md) for full function reference, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [Basic Recording](#basic-recording)
- [Cursor Indicator](#cursor-indicator)
- [How Recording Works](#how-recording-works)
- [Use Cases](#use-cases)
- [Best Practices](#best-practices)
- [Output Format](#output-format)
- [Limitations](#limitations)

## Basic Recording

Enable video recording when opening a session:

```bash
# Start with recording enabled
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://example.com",
  "record_video": true
}' | jq -r '.session_id')

# Perform actions
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e1"
}'

infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "fill", "ref": "@e2", "text": "test input"
}'

# Close to get the video
RESULT=$(infsh app run agent-browser --function close --session $SESSION --input '{}')
VIDEO=$(echo $RESULT | jq -r '.video')
echo "Video file: $VIDEO"
```

## Cursor Indicator

For demos and documentation, show a visible cursor that follows mouse movements:

```bash
SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://example.com",
  "record_video": true,
  "show_cursor": true
}' | jq -r '.session_id')
```

The cursor appears as a red dot that:
- Follows mouse movements in real-time
- Shows click feedback (shrinks on mousedown)
- Persists across page navigations
- Appears in both screenshots and video

This is especially useful for:
- Tutorial/documentation videos
- Debugging interaction issues
- Sharing recordings with non-technical stakeholders

## How Recording Works

1. **Start**: Pass `"record_video": true` in the `open` function
2. **Record**: All browser activity is captured throughout the session
3. **Stop**: Video is finalized when `close` is called
4. **Retrieve**: Video file is returned in the `close` response

The video captures:
- Page loads and navigations
- Element interactions (clicks, typing)
- Scrolling and animations
- Dynamic content changes

## Use Cases

### Debugging Failed Automation

```bash
#!/bin/bash
# Record automation for debugging

SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://app.example.com",
  "record_video": true
}' | jq -r '.session_id')

# Run automation
RESULT=$(infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e1"
}')

SUCCESS=$(echo $RESULT | jq -r '.success')
if [ "$SUCCESS" != "true" ]; then
  echo "Action failed!"
  echo "Message: $(echo $RESULT | jq -r '.message')"

  # Get video for debugging
  CLOSE_RESULT=$(infsh app run agent-browser --function close --session $SESSION --input '{}')
  echo "Debug video: $(echo $CLOSE_RESULT | jq -r '.video')"
  exit 1
fi

infsh app run agent-browser --function close --session $SESSION --input '{}'
```

### Documentation Generation

Record workflows for user documentation:

```bash
#!/bin/bash
# Record how-to video

SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://app.example.com/settings",
  "record_video": true,
  "width": 1920,
  "height": 1080
}' | jq -r '.session_id')

# Add pauses for clarity
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 1000
}'

# Step 1: Click settings
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e5"
}'
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 500
}'

# Step 2: Change setting
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e10"
}'
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 500
}'

# Step 3: Save
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e15"
}'
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "wait", "wait_ms": 1000
}'

# Get the video
RESULT=$(infsh app run agent-browser --function close --session $SESSION --input '{}')
echo "Documentation video: $(echo $RESULT | jq -r '.video')"
```

### Test Evidence for CI/CD

```bash
#!/bin/bash
# Record E2E test for CI artifacts

TEST_NAME="${1:-e2e-test}"

SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "'"$TEST_URL"'",
  "record_video": true
}' | jq -r '.session_id')

# Run test steps
run_test_steps $SESSION
TEST_RESULT=$?

# Always get video
CLOSE_RESULT=$(infsh app run agent-browser --function close --session $SESSION --input '{}')
VIDEO=$(echo $CLOSE_RESULT | jq -r '.video')

# Save to artifacts
if [ -n "$CI_ARTIFACTS_DIR" ]; then
  cp "$VIDEO" "$CI_ARTIFACTS_DIR/${TEST_NAME}.webm"
fi

exit $TEST_RESULT
```

### Monitoring and Auditing

```bash
#!/bin/bash
# Record automated task for audit trail

TASK_ID=$(date +%Y%m%d-%H%M%S)

SESSION=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://admin.example.com",
  "record_video": true
}' | jq -r '.session_id')

# Perform admin task
# ... automation steps ...

# Save recording
RESULT=$(infsh app run agent-browser --function close --session $SESSION --input '{}')
VIDEO=$(echo $RESULT | jq -r '.video')

# Archive for audit
mv "$VIDEO" "/audit/recordings/${TASK_ID}.webm"
echo "Audit recording saved: ${TASK_ID}.webm"
```

## Best Practices

### 1. Add Strategic Pauses

Pauses make videos easier to follow:

```bash
# After significant actions, add a pause
'{"action": "click", "ref": "@e1"}'
'{"action": "wait", "wait_ms": 500}'  # Let viewer see result
```

### 2. Use Larger Viewport for Documentation

```bash
'{"url": "...", "record_video": true, "width": 1920, "height": 1080}'
```

### 3. Handle Errors Gracefully

Always retrieve video even on failure:

```bash
cleanup() {
  if [ -n "$SESSION" ]; then
    infsh app run agent-browser --function close --session $SESSION --input '{}' 2>/dev/null
  fi
}
trap cleanup EXIT
```

### 4. Combine with Screenshots

Use screenshots for key frames, video for flow:

```bash
# Record overall flow
'{"record_video": true}'

# Capture key states
infsh app run agent-browser --function screenshot --session $SESSION --input '{
  "full_page": true
}'
```

### 5. Don't Record Sensitive Sessions

Avoid recording when handling credentials:

```bash
if [ "$CONTAINS_SENSITIVE_DATA" = "true" ]; then
  RECORD="false"
else
  RECORD="true"
fi

'{"url": "...", "record_video": '$RECORD'}'
```

## Output Format

- **Format**: WebM (VP8/VP9 codec)
- **Compatibility**: All modern browsers and video players
- **Quality**: Matches viewport size
- **Compression**: Efficient for screen content

## Limitations

1. **Session-level only** - Can't start/stop mid-session
2. **Memory usage** - Long sessions consume more memory
3. **File size** - Complex pages with animations produce larger files
4. **No audio** - Browser audio is not captured
5. **Returned on close** - Video only available after session ends
