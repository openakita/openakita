#!/bin/bash
# Template: Content Capture Workflow
# Purpose: Extract content from web pages (text, screenshots, video)
# Usage: ./capture-workflow.sh <url> [output-dir]
#
# Outputs:
#   - page-screenshot.json: Page screenshot data
#   - page-full-screenshot.json: Full page screenshot data
#   - page-elements.txt: Interactive elements with refs
#   - page-text.txt: All text content
#   - page-links.txt: All links on the page
#   - session-video.json: Video recording (if enabled)

set -euo pipefail

TARGET_URL="${1:?Usage: $0 <url> [output-dir]}"
OUTPUT_DIR="${2:-.}"

echo "Content capture: $TARGET_URL"
echo "Output directory: $OUTPUT_DIR"

mkdir -p "$OUTPUT_DIR"

# Cleanup handler
cleanup() {
  if [ -n "${SESSION_ID:-}" ]; then
    echo "Closing session..."
    CLOSE_RESULT=$(infsh app run agent-browser --function close --session $SESSION_ID --input '{}' 2>/dev/null || echo '{}')

    # Save video if available
    VIDEO=$(echo $CLOSE_RESULT | jq -r '.video // empty')
    if [ -n "$VIDEO" ]; then
      echo "$CLOSE_RESULT" > "$OUTPUT_DIR/session-video.json"
      echo "Video saved to: $OUTPUT_DIR/session-video.json"
    fi
  fi
}
trap cleanup EXIT

# ================================================================
# CONFIGURATION
# ================================================================
RECORD_VIDEO=false  # Set to true to record video
FULL_PAGE=true      # Set to true for full page screenshots
EXTRACT_LINKS=true  # Set to true to extract all links
SCROLL_PAGES=0      # Number of scroll actions for infinite scroll pages

# ================================================================
# CAPTURE WORKFLOW
# ================================================================

# Start session
echo "Opening page..."
RESULT=$(infsh app run agent-browser --function open --session new --input '{
  "url": "'"$TARGET_URL"'",
  "record_video": '$RECORD_VIDEO',
  "width": 1920,
  "height": 1080
}')
SESSION_ID=$(echo $RESULT | jq -r '.session_id')

# Get metadata
URL=$(echo $RESULT | jq -r '.url')
TITLE=$(echo $RESULT | jq -r '.title')
echo "Title: $TITLE"
echo "URL: $URL"

# Save elements
echo $RESULT | jq -r '.elements_text' > "$OUTPUT_DIR/page-elements.txt"
echo "Elements saved to: $OUTPUT_DIR/page-elements.txt"

# Handle infinite scroll (if configured)
if [ $SCROLL_PAGES -gt 0 ]; then
  echo "Scrolling through $SCROLL_PAGES pages..."
  for ((i=1; i<=SCROLL_PAGES; i++)); do
    infsh app run agent-browser --function interact --session $SESSION_ID --input '{
      "action": "scroll", "direction": "down", "scroll_amount": 800
    }' > /dev/null
    infsh app run agent-browser --function interact --session $SESSION_ID --input '{
      "action": "wait", "wait_ms": 1000
    }' > /dev/null
    echo "  Scrolled page $i/$SCROLL_PAGES"
  done

  # Re-snapshot after scrolling
  RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION_ID --input '{}')
fi

# Take viewport screenshot
echo "Taking viewport screenshot..."
infsh app run agent-browser --function screenshot --session $SESSION_ID --input '{}' > "$OUTPUT_DIR/page-screenshot.json"
echo "Screenshot saved to: $OUTPUT_DIR/page-screenshot.json"

# Take full page screenshot (if configured)
if [ "$FULL_PAGE" = true ]; then
  echo "Taking full page screenshot..."
  infsh app run agent-browser --function screenshot --session $SESSION_ID --input '{
    "full_page": true
  }' > "$OUTPUT_DIR/page-full-screenshot.json"
  echo "Full screenshot saved to: $OUTPUT_DIR/page-full-screenshot.json"
fi

# Extract all text content
echo "Extracting text content..."
RESULT=$(infsh app run agent-browser --function execute --session $SESSION_ID --input '{
  "code": "document.body.innerText"
}')
echo $RESULT | jq -r '.result' > "$OUTPUT_DIR/page-text.txt"
echo "Text saved to: $OUTPUT_DIR/page-text.txt"

# Extract all links (if configured)
if [ "$EXTRACT_LINKS" = true ]; then
  echo "Extracting links..."
  RESULT=$(infsh app run agent-browser --function execute --session $SESSION_ID --input '{
    "code": "Array.from(document.querySelectorAll(\"a[href]\")).map(a => a.href + \" | \" + (a.textContent || \"\").trim().slice(0,50)).join(\"\\n\")"
  }')
  echo $RESULT | jq -r '.result' > "$OUTPUT_DIR/page-links.txt"
  echo "Links saved to: $OUTPUT_DIR/page-links.txt"
fi

# ================================================================
# CUSTOM EXTRACTION: Add your specific extraction logic here
# ================================================================

# Example: Extract specific elements by selector
# RESULT=$(infsh app run agent-browser --function execute --session $SESSION_ID --input '{
#   "code": "Array.from(document.querySelectorAll(\"h2\")).map(h => h.textContent).join(\"\\n\")"
# }')
# echo $RESULT | jq -r '.result' > "$OUTPUT_DIR/headings.txt"

# Example: Extract JSON data from script tag
# RESULT=$(infsh app run agent-browser --function execute --session $SESSION_ID --input '{
#   "code": "JSON.parse(document.querySelector(\"script[type=application/json]\").textContent)"
# }')
# echo $RESULT | jq '.result' > "$OUTPUT_DIR/json-data.json"

# Example: Extract table data
# RESULT=$(infsh app run agent-browser --function execute --session $SESSION_ID --input '{
#   "code": "Array.from(document.querySelectorAll(\"table tr\")).map(tr => Array.from(tr.cells).map(td => td.textContent.trim()).join(\",\")).join(\"\\n\")"
# }')
# echo $RESULT | jq -r '.result' > "$OUTPUT_DIR/table-data.csv"

# ================================================================
# SUMMARY
# ================================================================
echo ""
echo "Capture complete!"
echo "Files created:"
ls -la "$OUTPUT_DIR"/*.txt "$OUTPUT_DIR"/*.json 2>/dev/null || true
