#!/bin/bash
# Template: Form Automation Workflow
# Purpose: Fill and submit web forms with validation
# Usage: ./form-automation.sh <form-url>
#
# This template demonstrates the snapshot-interact-verify pattern:
# 1. Navigate to form
# 2. Snapshot to get element refs
# 3. Fill fields using refs
# 4. Submit and verify result
#
# Customize: Update the refs (@e1, @e2, etc.) based on your form's snapshot output

set -euo pipefail

FORM_URL="${1:?Usage: $0 <form-url>}"

echo "Form automation: $FORM_URL"

# Cleanup handler
cleanup() {
  if [ -n "${SESSION_ID:-}" ]; then
    infsh app run agent-browser --function close --session $SESSION_ID --input '{}' 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Step 1: Navigate to form
echo "Opening form..."
RESULT=$(infsh app run agent-browser --function open --session new --input '{
  "url": "'"$FORM_URL"'"
}')
SESSION_ID=$(echo $RESULT | jq -r '.session_id')

# Step 2: Display form structure
echo ""
echo "Form elements:"
echo "---"
echo $RESULT | jq -r '.elements_text'
echo "---"
echo ""

# ================================================================
# DISCOVERY MODE: Shows form structure
# After running once, update the FORM FILL section below with your refs
# then delete or comment out this section
# ================================================================
echo "Discovery mode: Form structure shown above"
echo ""
echo "Next steps:"
echo "  1. Note the refs for your form fields (e.g., @e1 for name, @e2 for email)"
echo "  2. Update the FORM FILL section below"
echo "  3. Set environment variables for form data"
echo "  4. Comment out this discovery section"
echo ""
exit 0

# ================================================================
# FORM FILL: Uncomment and customize after discovery
# ================================================================
# echo "Filling form..."
#
# # Text input
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "fill", "ref": "@e1", "text": "'"${FORM_NAME:-John Doe}"'"
# }'
#
# # Email input
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "fill", "ref": "@e2", "text": "'"${FORM_EMAIL:-john@example.com}"'"
# }'
#
# # Dropdown/select
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "select", "ref": "@e3", "text": "Option 1"
# }'
#
# # Checkbox
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "check", "ref": "@e4"
# }'
#
# # Textarea
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "fill", "ref": "@e5", "text": "'"${FORM_MESSAGE:-Hello, this is a test message.}"'"
# }'
#
# # Submit button
# echo "Submitting form..."
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "click", "ref": "@e6"
# }'
#
# # Wait for submission
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "wait", "wait_ms": 2000
# }'
#
# # Step 3: Verify result
# echo ""
# echo "Verifying submission..."
# RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION_ID --input '{}')
#
# URL=$(echo $RESULT | jq -r '.url')
# TITLE=$(echo $RESULT | jq -r '.title')
# echo "Final URL: $URL"
# echo "Page title: $TITLE"
#
# # Check for success indicators
# ELEMENTS=$(echo $RESULT | jq -r '.elements_text')
# if echo "$ELEMENTS" | grep -qi "thank you\|success\|submitted"; then
#   echo "SUCCESS: Form submitted successfully"
# elif echo "$URL" | grep -qi "error\|fail"; then
#   echo "ERROR: Form submission may have failed"
#   exit 1
# else
#   echo "UNKNOWN: Check the result manually"
# fi
#
# # Optional: Capture evidence
# infsh app run agent-browser --function screenshot --session $SESSION_ID --input '{
#   "full_page": true
# }' > form-result-screenshot.json
# echo "Screenshot saved to form-result-screenshot.json"

echo "Done"
