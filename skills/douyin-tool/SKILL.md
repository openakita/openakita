---
name: openakita/skills@douyin-tool
description: Douyin (TikTok China) content toolkit for video script writing, short video optimization, BGM recommendations, caption generation, trending topic analysis, and video download/info extraction workflows.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Douyin Video Toolkit

An end-to-end Douyin short video toolkit covering video script writing, BGM recommendations, subtitle/caption generation, trending topic analysis, and video info extraction to help you efficiently create and operate Douyin content.

## When to Use

- Writing short video scripts (15s / 60s / 3min)
- Extracting Douyin video information (title, tags, music, etc.)
- Downloading Douyin videos without watermarks
- Generating video subtitles and copy
- Analyzing Douyin trending topics and trends
- Planning BGM selection strategies
- Short video content matrix planning
- Video copy A/B testing strategies

## Video Script Writing

### 1. The Golden 3-Second Rule

Douyin users take only **3 seconds** on average to decide whether to keep watching. The opening must grab attention immediately.

**7 Hook Templates:**

| # | Hook Type | Template | Example |
|---|-----------|----------|---------|
| 1 | Counterintuitive | `[Most people don't know]...` | `90% of people wash their faces wrong` |
| 2 | Number impact | `[Number] + [shocking result]` | `Spent only 50 yuan, looks like 50,000` |
| 3 | Pain point strike | `If you also [pain point]...` | `If you can never save money, watch this` |
| 4 | Suspense teaser | `Watch till the end and you'll [surprise]...` | `Don't scroll away, there's a twist at the end` |
| 5 | Identity call-out | `[Target audience] must watch` | `5 productivity apps every office worker needs` |
| 6 | Controversy starter | `Which is better, [A or B]?` | `iPhone or Xiaomi — which one is worth buying?` |
| 7 | Result showcase | Show the final result first | Show the transformed result, then explain the process |

### 2. Script Structure Templates

**15-second video (single message):**
```
[0-3s]   Hook: Grab attention in one sentence
[3-12s]  Core: Key information / demo / showcase
[12-15s] CTA: Guide interaction ("Follow to learn more")
```

**60-second video (standard tutorial/recommendation):**
```
[0-3s]   Hook: Pain point or result showcase
[3-10s]  Background: Why this is needed / brief intro
[10-45s] Core: Step-by-step explanation / product demo (3-5 key points)
[45-55s] Summary: Restate core value
[55-60s] CTA: "Like and save — you'll need this later"
```

**3-minute video (deep content / story):**
```
[0-3s]    Hook: Most exciting clip upfront
[3-20s]   Intro: Background story / problem statement
[20-60s]  Develop: First core point + case study
[60-100s] Deepen: Second point + twist
[100-140s] Climax: Most valuable info / reversal
[140-165s] Summary + CTA
[165-180s] Bonus / next episode teaser (optional)
```

### 3. Script Writing Standards

**Language style:**
- Conversational, like chatting with a friend
- Use short sentences, each no more than 15 characters
- Use second person ("you") to create closeness
- Avoid formal and overly literary expressions
- Use current internet slang appropriately (but not excessively)

**Pacing control:**
- Speaking rate: 3-4 characters per second (standard Mandarin)
- 15s video: approximately 45-60 characters
- 60s video: approximately 180-240 characters
- 3min video: approximately 540-720 characters

**Script format:**
```markdown
## Video Script: [Title]

### Basic Info
- Duration: [X seconds]
- Type: [Tutorial / Recommendation / Story / Comedy]
- Target audience: [description]

### Storyboard

| Time | Scene | Dialogue/Subtitle | Sound/BGM |
|------|-------|-------------------|-----------|
| 0-3s | Close-up / face-to-camera | [Opening hook] | BGM fades in |
| 3-10s | [Scene description] | [Dialogue] | [Sound effect] |
| ... | ... | ... | ... |

### Subtitle Highlights
[Keywords that need bolding / color changes]

### Shooting Tips
[Camera angle, lighting, props, etc.]
```

## BGM Recommendation Strategy

### 1. BGM Selection Principles

| Principle | Description |
|-----------|-------------|
| **Match content mood** | Light BGM for tutorials, emotional BGM for stories |
| **Follow trending charts** | Use currently popular Douyin music to boost recommendations |
| **Rhythm sync points** | Sync BGM beat drops with scene transitions |
| **Volume balance** | BGM volume should not exceed 30% of vocals |
| **Copyright safety** | Prioritize music from Douyin's music library |

### 2. Content Type and BGM Style Matching

| Content Type | BGM Style | Tempo | Recommended Search Method |
|--------------|-----------|-------|--------------------------|
| Tutorial / tips | Light electronic / Lo-fi | 100-120 BPM | Search "tutorial background music" |
| Food cooking | Acoustic guitar / light music | 80-100 BPM | Search "food BGM" |
| Fitness / sports | Energetic electronic / Hip-hop | 120-140 BPM | Search "workout music" |
| Emotional story | Piano / strings | 60-80 BPM | Search "emotional soundtrack" |
| Comedy / prank | Catchy / contrast sound effects | Variable | Search current meme music |
| Fashion / transformation | Rhythmic pop | 100-130 BPM | Reference trending transformation videos |
| Travel / Vlog | Folk / indie | 90-110 BPM | Search "travel BGM" |
| Pets | Cute / lively | 100-120 BPM | Search "cute pet music" |

### 3. Tracking Hot BGM Methods

```
Step 1: Open Douyin → Search → Music Chart
  ↓
Step 2: Record this week's Top 20 popular tracks
  ↓
Step 3: Filter 3-5 tracks that match your content type
  ↓
Step 4: Preview and select the best rhythm match
  ↓
Step 5: Note the track name and usage count (to assess if trending upward)
```

**Optimal timing:** Best window is when usage is between 100K-1M. Below 100K may not have traction; above 1M may be overused.

## Subtitle/Copy Generation

### 1. Subtitle Standards

**Styling recommendations:**

| Parameter | Recommended Value |
|-----------|-------------------|
| Font | Source Han Sans / PingFang |
| Size | 5-7% of screen width |
| Color | White with black stroke |
| Position | Lower 1/4 of the frame |
| Chars per line | No more than 15 |
| Display duration | 0.25-0.3 seconds per character |

**Key subtitle emphasis:**
- Use yellow/red for key numbers
- Use zoom effect for keywords
- Use animation effects (e.g., shake) for transition words
- Use special colors for brand/product names

### 2. Video Title Copy

**Title structure formula:**
```
[Hook word] + [Core message] + [Benefit / suspense]
```

**High-performing title templates:**

| Template | Example |
|----------|---------|
| `Shocking! [Unexpected discovery]` | `Shocking! Using your AC this way saves half the electricity bill` |
| `[Number] things [audience] don't know about [topic]` | `5 Excel tricks office workers don't know about` |
| `Never [wrong approach]` | `Never eat these 3 fruits on an empty stomach` |
| `[Before] vs [After]` | `100 days before vs 100 days after fitness` |
| `[Price comparison] + [Result]` | `What's the difference between a 9.9 and 999 facial mask?` |
| `Turns out [common thing] can also be used this way` | `Turns out your phone calculator has this hidden feature` |

### 3. Comment Section Copy

**Engagement-driving comment templates:**

```
Pinned comment:
"Bet you didn't know about #[X]! Go try it 👀"
"What type of content do you want to see next? Leave a comment 📝"
"Same-style product link is in our showcase, find it yourself 🔍"

Engagement comments:
"Type 1 for [content type A], type 2 for [content type B]"
"What's your city called? Reply below 🏙️"
"Guess how much this costs? Closest guess gets a DM on where to buy"
```

## Trending Topic Analysis Workflow

### Analysis Process

```
Step 1: Information Gathering
  - Douyin hot search list (updated daily)
  - Douyin content inspiration page
  - Industry-specific trending topics
  - Holiday/seasonal topic calendar
  ↓
Step 2: Topic Filtering
  Filter dimensions:
  - Relevance to account positioning (>70%)
  - Topic popularity (upward trends prioritized)
  - Competition level (avoid oversaturated topics)
  - Timeliness (can produce content within the trend window)
  ↓
Step 3: Content Angle Discovery
  Find differentiated angles for each topic:
  - Contrarian view (everyone says good, you point out flaws)
  - Vertical deep dive (general topic + professional perspective)
  - Personal experience (tested / lessons learned / comparison)
  - Data-driven analysis (speak with numbers)
  ↓
Step 4: Content Planning
  Create a 3-7 day content publishing schedule
  - 1 trend-chasing video (timely)
  - 2 evergreen content pieces (long-term value)
  - 1 engagement video (boost interaction rate)
```

### Topic Calendar Template

```markdown
## [Month] Douyin Content Calendar

### Fixed Events
| Date | Event | Content Direction |
|------|-------|-------------------|
| 3/8 | Women's Day | Female empowerment / recommended products |
| 3/12 | Arbor Day | Eco-friendly / plants / outdoors |
| 3/15 | Consumer Rights Day | Pitfall avoidance guide / rights awareness |

### Seasonal Topics
- Seasonal skincare
- Spring fashion
- Spring outing guide

### Industry Cycle Topics
[Fill in based on your vertical sector]
```

## Video Download & Info Extraction

### 1. Video Info Extraction

Extract the following information from a Douyin video link:

```markdown
## Video Information

### Basic Info
- Title: [Video title / caption]
- Author: [Nickname] (@[Douyin ID])
- Published: [Date]
- Duration: [X seconds]

### Engagement Data
- Likes: [count]
- Comments: [count]
- Saves: [count]
- Shares: [count]

### Content Tags
- Hashtags: [#tag list]
- Music: [Track name - Artist]
- Challenge: [If applicable]

### Full Caption
[Full caption text]
```

### 2. Video Download Workflow

**Method 1: API Tool Call**

If relevant MCP tools (e.g., video downloader) are installed, call directly:

```
Step 1: Get the share link
  - In Douyin app, click "Share" → "Copy Link"
  - Link format: https://v.douyin.com/xxxxx/
  ↓
Step 2: Resolve the actual video URL
  - Parse short link to get video ID
  - Request video info API to get watermark-free URL
  ↓
Step 3: Download the video file
  - Use the watermark-free URL to download
  - Save to the specified directory
```

**Method 2: Manual Instructions**

```
Step 1: Copy the Douyin share link
Step 2: Open the link in a browser
Step 3: Use browser DevTools (F12) → Network panel
Step 4: Filter for video type requests
Step 5: Find the .mp4 resource URL
Step 6: Right-click → Open in new tab → Save As
```

### 3. Batch Analysis

Batch analysis of competitor or similar account videos:

```markdown
## Competitor Video Analysis Report

### Account Overview
- Account name:
- Followers:
- Average likes:
- Posting frequency:

### Top 10 Liked Videos Analysis
| Rank | Title | Likes | Opening Type | BGM | Duration |
|------|-------|-------|-------------|-----|----------|
| 1 | ... | ... | Counterintuitive | ... | 15s |
| 2 | ... | ... | Number impact | ... | 60s |

### Pattern Summary
- Common traits of high-performing content:
- Best posting time:
- Most popular content type:
- BGM usage patterns:

### Actionable Directions
1. [Direction 1]
2. [Direction 2]
3. [Direction 3]
```

## Publishing Optimization

### Best Posting Times

| Time Slot | Suitable Content | Description |
|-----------|-----------------|-------------|
| 7:00-9:00 | Knowledge / news / morning greeting | Commute time, fragment browsing |
| 12:00-13:00 | Light / food / entertainment | Lunch break |
| 17:00-19:00 | Tutorial / recommendation / review | After work, patient for longer content |
| 20:00-22:00 | All types | Prime evening slot, maximum traffic |
| 22:00-23:30 | Emotional / story / deep content | Pre-sleep, suitable for heartfelt content |

### DOU+ Promotion Strategy

| Stage | Budget | Strategy |
|-------|--------|----------|
| Testing | ¥100 | Observe 6h of organic data before deciding to promote |
| Acceleration | ¥300-500 | Boost videos showing strong organic performance |
| Explosion | ¥1000+ | Heavy promotion on proven viral content |

**Promotion criteria:**
- Completion rate > 30%: Worth promoting
- Like rate > 3%: Viral potential
- Comment rate > 0.5%: Good engagement
- Share rate > 0.3%: Strong spreadability

## Common Mistakes

| Mistake | Correct Approach |
|---------|-----------------|
| No hook in the first 3 seconds | First word / frame must grab attention |
| Videos too long and scattered | Each video covers only one core point |
- Background music too loud | Keep BGM at 20-30% of vocal volume |
- Subtitles too small | Font size at least 5% of screen width |
- Publishing next video without checking analytics | Analyze 24h data after each video |
- Chasing trends regardless of positioning | Only hop on trends relevant to your positioning |
- Ignoring comments after posting | Actively reply to comments in the first 30 minutes |

## Output Format

When generating Douyin content, use the following output structure:

```markdown
## 🎬 Douyin Video Proposal

### Basic Info
- Video type: [Tutorial / Recommendation / Story / Other]
- Suggested duration: [X seconds]
- Target audience: [description]

### Title Options (pick 1 of 3)
1. [Option 1]
2. [Option 2]
3. [Option 3]

### Storyboard
| Time | Scene | Dialogue | Sound/BGM |
|------|-------|----------|-----------|
| ... | ... | ... | ... |

### Subtitle Highlights
[Key subtitles to emphasize]

### Hashtags
[6-8 tags]

### BGM Recommendation
- Style: [description]
- Recommended search keyword: [keyword]
- Tempo: [BPM range]

### Publishing Suggestions
- Recommended time: [specific time slot]
- Cover frame: [suggest which second's frame to use]
- Comment engagement: [prompt phrase]
```
