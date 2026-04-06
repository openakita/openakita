---
name: ai-automation-workflows
description: "Build automated AI workflows combining multiple models and services. Patterns: batch processing, scheduled tasks, event-driven pipelines, agent loops. Tools: inference.sh CLI, bash scripting, Python SDK, webhook integration. Use for: content automation, data processing, monitoring, scheduled generation. Triggers: ai automation, workflow automation, batch processing, ai pipeline, automated content, scheduled ai, ai cron, ai batch job, automated generation, ai workflow, content at scale, automation script, ai orchestration"
allowed-tools: Bash(infsh *)
---

# AI Automation Workflows

Build automated AI workflows via [inference.sh](https://inference.sh) CLI.

![AI Automation Workflows](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg0v0nz7wv0qwqjtq1cam52z.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Simple automation: Generate daily image
infsh app run falai/flux-dev --input '{
  "prompt": "Inspirational quote background, minimalist design, date: '"$(date +%Y-%m-%d)"'"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Automation Patterns

### Pattern 1: Batch Processing

Process multiple items with the same workflow.

```bash
#!/bin/bash
# batch_images.sh - Generate images for multiple prompts

PROMPTS=(
  "Mountain landscape at sunrise"
  "Ocean waves at sunset"
  "Forest path in autumn"
  "Desert dunes at night"
)

for prompt in "${PROMPTS[@]}"; do
  echo "Generating: $prompt"
  infsh app run falai/flux-dev --input "{
    \"prompt\": \"$prompt, professional photography, 4K\"
  }" > "output_${prompt// /_}.json"
  sleep 2  # Rate limiting
done
```

### Pattern 2: Sequential Pipeline

Chain multiple AI operations.

```bash
#!/bin/bash
# content_pipeline.sh - Full content creation pipeline

TOPIC="AI in healthcare"

# Step 1: Research
echo "Researching..."
RESEARCH=$(infsh app run tavily/search-assistant --input "{
  \"query\": \"$TOPIC latest developments\"
}")

# Step 2: Write article
echo "Writing article..."
ARTICLE=$(infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"Write a 500-word blog post about $TOPIC based on: $RESEARCH\"
}")

# Step 3: Generate image
echo "Generating image..."
IMAGE=$(infsh app run falai/flux-dev --input "{
  \"prompt\": \"Blog header image for article about $TOPIC, modern, professional\"
}")

# Step 4: Generate social post
echo "Creating social post..."
SOCIAL=$(infsh app run openrouter/claude-haiku-45 --input "{
  \"prompt\": \"Write a Twitter thread (5 tweets) summarizing: $ARTICLE\"
}")

echo "Pipeline complete!"
```

### Pattern 3: Parallel Processing

Run multiple operations simultaneously.

```bash
#!/bin/bash
# parallel_generation.sh - Generate multiple assets in parallel

# Start all jobs in background
infsh app run falai/flux-dev --input '{"prompt": "Hero image..."}' > hero.json &
PID1=$!

infsh app run falai/flux-dev --input '{"prompt": "Feature image 1..."}' > feature1.json &
PID2=$!

infsh app run falai/flux-dev --input '{"prompt": "Feature image 2..."}' > feature2.json &
PID3=$!

# Wait for all to complete
wait $PID1 $PID2 $PID3
echo "All images generated!"
```

### Pattern 4: Conditional Workflow

Branch based on results.

```bash
#!/bin/bash
# conditional_workflow.sh - Process based on content analysis

INPUT_TEXT="$1"

# Analyze content
ANALYSIS=$(infsh app run openrouter/claude-haiku-45 --input "{
  \"prompt\": \"Classify this text as: positive, negative, or neutral. Return only the classification.\n\n$INPUT_TEXT\"
}")

# Branch based on result
case "$ANALYSIS" in
  *positive*)
    echo "Generating celebration image..."
    infsh app run falai/flux-dev --input '{"prompt": "Celebration, success, happy"}'
    ;;
  *negative*)
    echo "Generating supportive message..."
    infsh app run openrouter/claude-sonnet-45 --input "{
      \"prompt\": \"Write a supportive, encouraging response to: $INPUT_TEXT\"
    }"
    ;;
  *)
    echo "Generating neutral acknowledgment..."
    ;;
esac
```

### Pattern 5: Retry with Fallback

Handle failures gracefully.

```bash
#!/bin/bash
# retry_workflow.sh - Retry failed operations

generate_with_retry() {
  local prompt="$1"
  local max_attempts=3
  local attempt=1

  while [ $attempt -le $max_attempts ]; do
    echo "Attempt $attempt..."

    result=$(infsh app run falai/flux-dev --input "{\"prompt\": \"$prompt\"}" 2>&1)

    if [ $? -eq 0 ]; then
      echo "$result"
      return 0
    fi

    echo "Failed, retrying..."
    ((attempt++))
    sleep $((attempt * 2))  # Exponential backoff
  done

  # Fallback to different model
  echo "Falling back to alternative model..."
  infsh app run google/imagen-3 --input "{\"prompt\": \"$prompt\"}"
}

generate_with_retry "A beautiful sunset over mountains"
```

## Scheduled Automation

### Cron Job Setup

```bash
# Edit crontab
crontab -e

# Daily content generation at 9 AM
0 9 * * * /path/to/daily_content.sh >> /var/log/ai-automation.log 2>&1

# Weekly report every Monday at 8 AM
0 8 * * 1 /path/to/weekly_report.sh >> /var/log/ai-automation.log 2>&1

# Every 6 hours: social media content
0 */6 * * * /path/to/social_content.sh >> /var/log/ai-automation.log 2>&1
```

### Daily Content Script

```bash
#!/bin/bash
# daily_content.sh - Run daily at 9 AM

DATE=$(date +%Y-%m-%d)
OUTPUT_DIR="/output/$DATE"
mkdir -p "$OUTPUT_DIR"

# Generate daily quote image
infsh app run falai/flux-dev --input '{
  "prompt": "Motivational quote background, minimalist, morning vibes"
}' > "$OUTPUT_DIR/quote_image.json"

# Generate daily tip
infsh app run openrouter/claude-haiku-45 --input '{
  "prompt": "Give me one actionable productivity tip for today. Be concise."
}' > "$OUTPUT_DIR/daily_tip.json"

# Post to social (optional)
# infsh app run twitter/post-tweet --input "{...}"

echo "Daily content generated: $DATE"
```

## Monitoring and Logging

### Logging Wrapper

```bash
#!/bin/bash
# logged_workflow.sh - With comprehensive logging

LOG_FILE="/var/log/ai-workflow-$(date +%Y%m%d).log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Starting workflow"

# Track execution time
START_TIME=$(date +%s)

# Run workflow
log "Generating image..."
RESULT=$(infsh app run falai/flux-dev --input '{"prompt": "test"}' 2>&1)
STATUS=$?

if [ $STATUS -eq 0 ]; then
  log "Success: Image generated"
else
  log "Error: $RESULT"
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
log "Completed in ${DURATION}s"
```

### Error Alerting

```bash
#!/bin/bash
# monitored_workflow.sh - With error alerts

run_with_alert() {
  local result
  result=$("$@" 2>&1)
  local status=$?

  if [ $status -ne 0 ]; then
    # Send alert (webhook, email, etc.)
    curl -X POST "https://your-webhook.com/alert" \
      -H "Content-Type: application/json" \
      -d "{\"error\": \"$result\", \"command\": \"$*\"}"
  fi

  echo "$result"
  return $status
}

run_with_alert infsh app run falai/flux-dev --input '{"prompt": "test"}'
```

## Python SDK Automation

```python
#!/usr/bin/env python3
# automation.py - Python-based workflow

import subprocess
import json
from datetime import datetime
from pathlib import Path

def run_infsh(app_id: str, input_data: dict) -> dict:
    """Run inference.sh app and return result."""
    result = subprocess.run(
        ["infsh", "app", "run", app_id, "--input", json.dumps(input_data)],
        capture_output=True,
        text=True
    )
    return json.loads(result.stdout) if result.returncode == 0 else None

def daily_content_pipeline():
    """Generate daily content."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path(f"output/{date_str}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate image
    image = run_infsh("falai/flux-dev", {
        "prompt": f"Daily inspiration for {date_str}, beautiful, uplifting"
    })
    (output_dir / "image.json").write_text(json.dumps(image))

    # Generate caption
    caption = run_infsh("openrouter/claude-haiku-45", {
        "prompt": "Write an inspiring caption for a daily motivation post. 2-3 sentences."
    })
    (output_dir / "caption.json").write_text(json.dumps(caption))

    print(f"Generated content for {date_str}")

if __name__ == "__main__":
    daily_content_pipeline()
```

## Workflow Templates

### Content Calendar Automation

```bash
#!/bin/bash
# content_calendar.sh - Generate week of content

TOPICS=("productivity" "wellness" "technology" "creativity" "leadership")
DAYS=("Monday" "Tuesday" "Wednesday" "Thursday" "Friday")

for i in "${!DAYS[@]}"; do
  DAY=${DAYS[$i]}
  TOPIC=${TOPICS[$i]}

  echo "Generating $DAY content about $TOPIC..."

  # Image
  infsh app run falai/flux-dev --input "{
    \"prompt\": \"$TOPIC theme, $DAY motivation, social media style\"
  }" > "content/${DAY}_image.json"

  # Caption
  infsh app run openrouter/claude-haiku-45 --input "{
    \"prompt\": \"Write a $DAY motivation post about $TOPIC. Include hashtags.\"
  }" > "content/${DAY}_caption.json"
done
```

### Data Processing Pipeline

```bash
#!/bin/bash
# data_processing.sh - Process and analyze data files

INPUT_DIR="./data/raw"
OUTPUT_DIR="./data/processed"

for file in "$INPUT_DIR"/*.txt; do
  filename=$(basename "$file" .txt)

  # Analyze content
  infsh app run openrouter/claude-haiku-45 --input "{
    \"prompt\": \"Analyze this data and provide key insights in JSON format: $(cat $file)\"
  }" > "$OUTPUT_DIR/${filename}_analysis.json"

done
```

## Best Practices

1. **Rate limiting** - Add delays between API calls
2. **Error handling** - Always check return codes
3. **Logging** - Track all operations
4. **Idempotency** - Design for safe re-runs
5. **Monitoring** - Alert on failures
6. **Backups** - Save intermediate results
7. **Timeouts** - Set reasonable limits

## Related Skills

```bash
# Content pipelines
npx skills add inference-sh/skills@ai-content-pipeline

# RAG pipelines
npx skills add inference-sh/skills@ai-rag-pipeline

# Social media automation
npx skills add inference-sh/skills@ai-social-media-content

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

Browse all apps: `infsh app list`
