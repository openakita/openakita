#!/bin/bash
# Template: Authenticated Session Workflow
# Purpose: Login once, perform actions, clean up
# Usage: ./authenticated-session.sh <login-url>
#
# Environment variables:
#   APP_USERNAME - Login username/email
#   APP_PASSWORD - Login password
#
# Two modes:
#   1. Discovery mode (default): Shows login form structure
#   2. Login mode: Performs actual login after you update refs
#
# Setup steps:
#   1. Run once to see form structure (discovery mode)
#   2. Update refs in LOGIN FLOW section below
#   3. Set APP_USERNAME and APP_PASSWORD
#   4. Comment out the DISCOVERY section

set -euo pipefail

LOGIN_URL="${1:?Usage: $0 <login-url>}"

echo "Authentication workflow: $LOGIN_URL"

# Cleanup handler
cleanup() {
  if [ -n "${SESSION_ID:-}" ]; then
    echo "Closing session..."
    infsh app run agent-browser --function close --session $SESSION_ID --input '{}' 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ================================================================
# DISCOVERY MODE: Shows login form structure
# Delete this section after setup
# ================================================================
echo "Opening login page..."
RESULT=$(infsh app run agent-browser --function open --session new --input '{
  "url": "'"$LOGIN_URL"'"
}')
SESSION_ID=$(echo $RESULT | jq -r '.session_id')

echo ""
echo "Login form structure:"
echo "---"
echo $RESULT | jq -r '.elements_text'
echo "---"
echo ""
echo "Discovery mode complete."
echo ""
echo "Next steps:"
echo "  1. Identify the refs: username=@e?, password=@e?, submit=@e?"
echo "  2. Update the LOGIN FLOW section below with your refs"
echo "  3. Set environment variables:"
echo "     export APP_USERNAME='your-username'"
echo "     export APP_PASSWORD='your-password'"
echo "  4. Comment out this DISCOVERY MODE section"
echo ""
exit 0

# ================================================================
# LOGIN FLOW: Uncomment and customize after discovery
# ================================================================
# : "${APP_USERNAME:?Set APP_USERNAME environment variable}"
# : "${APP_PASSWORD:?Set APP_PASSWORD environment variable}"
#
# echo "Opening login page..."
# RESULT=$(infsh app run agent-browser --function open --session new --input '{
#   "url": "'"$LOGIN_URL"'",
#   "record_video": false
# }')
# SESSION_ID=$(echo $RESULT | jq -r '.session_id')
#
# echo "Filling credentials..."
# # Update @e1, @e2, @e3 to match your form
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "fill", "ref": "@e1", "text": "'"$APP_USERNAME"'"
# }'
#
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "fill", "ref": "@e2", "text": "'"$APP_PASSWORD"'"
# }'
#
# echo "Submitting..."
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "click", "ref": "@e3"
# }'
#
# # Wait for redirect
# infsh app run agent-browser --function interact --session $SESSION_ID --input '{
#   "action": "wait", "wait_ms": 3000
# }'
#
# # Verify login succeeded
# RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION_ID --input '{}')
# URL=$(echo $RESULT | jq -r '.url')
#
# if [[ "$URL" == *"/login"* ]] || [[ "$URL" == *"/signin"* ]]; then
#   echo "ERROR: Login failed - still on login page"
#   echo "URL: $URL"
#   infsh app run agent-browser --function screenshot --session $SESSION_ID --input '{}' > login-failed.json
#   exit 1
# fi
#
# echo "Login successful!"
# echo "Current URL: $URL"
# echo ""
#
# # ================================================================
# # AUTHENTICATED ACTIONS: Add your post-login automation here
# # ================================================================
# echo "Performing authenticated actions..."
#
# # Example: Navigate to dashboard
# # infsh app run agent-browser --function interact --session $SESSION_ID --input '{
# #   "action": "goto", "url": "https://app.example.com/dashboard"
# # }'
#
# # Example: Click a menu item
# # infsh app run agent-browser --function interact --session $SESSION_ID --input '{
# #   "action": "click", "ref": "@e5"
# # }'
#
# # Example: Extract data
# # RESULT=$(infsh app run agent-browser --function execute --session $SESSION_ID --input '{
# #   "code": "document.querySelector(\".user-data\").textContent"
# # }')
# # echo "Data: $(echo $RESULT | jq -r '.result')"
#
# # Example: Take screenshot of authenticated page
# # infsh app run agent-browser --function screenshot --session $SESSION_ID --input '{
# #   "full_page": true
# # }' > authenticated-page.json
#
# echo ""
# echo "Authenticated session complete"
