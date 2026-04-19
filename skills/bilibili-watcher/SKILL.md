---
name: openakita/skills@bilibili-watcher
description: Extract subtitles and transcripts from Bilibili and YouTube videos. Use when the user wants to get subtitles from Bilibili or YouTube, extract Chinese/Japanese video transcripts, watch member-only Bilibili content, or perform Q&A on video content. Supports dual-platform subtitle extraction with yt-dlp.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
  based_on: openclaw/skills/bilibili-youtube-watcher
---

# Bilibili & YouTube Watcher — Dual-Platform Subtitle Extraction

Extract subtitles and transcripts from Bilibili and YouTube videos. Supports multiple languages, member-only content, and content Q&A.

## When to Use This Skill

- User shares a Bilibili link and wants subtitles or a summary
- User shares a YouTube link and wants transcript extraction via yt-dlp
- User needs subtitles from member-only Bilibili videos
- User wants to search or query content within a video's transcript
- User wants to compare subtitles across languages
- User needs to extract subtitles from videos with hardcoded subs (OCR not included — only soft subs)
- User wants batch subtitle extraction from a playlist or series

## Prerequisites

### Install yt-dlp

yt-dlp is a feature-rich command-line audio/video downloader that also extracts subtitles.

**Via pip (recommended):**
```bash
pip install yt-dlp
```

**Via package manager:**
```bash
# macOS
brew install yt-dlp

# Ubuntu/Debian
sudo apt install yt-dlp

# Windows (scoop)
scoop install yt-dlp
```

**Verify installation:**
```bash
yt-dlp --version
```

### Optional: ffmpeg

Some subtitle formats require ffmpeg for conversion:

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows (scoop)
scoop install ffmpeg
```

### Cookie Setup for Bilibili Member Videos

Bilibili member-only content requires authentication cookies.

**Method 1: Export cookies from browser**

Install a browser extension like "Get cookies.txt LOCALLY" and export cookies for `bilibili.com`:

```bash
yt-dlp --cookies cookies.txt "https://www.bilibili.com/video/BV..."
```

**Method 2: Use browser cookies directly**

```bash
yt-dlp --cookies-from-browser chrome "https://www.bilibili.com/video/BV..."
yt-dlp --cookies-from-browser firefox "https://www.bilibili.com/video/BV..."
yt-dlp --cookies-from-browser edge "https://www.bilibili.com/video/BV..."
```

> **Security note**: Cookie files contain your login session. Do not share them or commit them to version control.

---

## Instructions

### Step 1: Identify the Platform and URL Format

#### Bilibili URL Formats

| Format | Example |
|---|---|
| Standard BV | `https://www.bilibili.com/video/BV1xx411c7mD` |
| With page | `https://www.bilibili.com/video/BV1xx411c7mD?p=2` |
| Short link | `https://b23.tv/aBcDeFg` |
| Bangumi | `https://www.bilibili.com/bangumi/play/ep12345` |
| Old AV format | `https://www.bilibili.com/video/av12345` |
| Mobile | `https://m.bilibili.com/video/BV1xx411c7mD` |

#### YouTube URL Formats

| Format | Example |
|---|---|
| Standard | `https://www.youtube.com/watch?v=VIDEO_ID` |
| Short | `https://youtu.be/VIDEO_ID` |
| Embed | `https://www.youtube.com/embed/VIDEO_ID` |
| Shorts | `https://www.youtube.com/shorts/VIDEO_ID` |

### Step 2: Extract Subtitles

#### Bilibili Subtitle Extraction

**List available subtitles:**
```bash
yt-dlp --list-subs "https://www.bilibili.com/video/BV..."
```

**Download subtitles only (no video):**
```bash
# Download all available subtitles
yt-dlp --write-sub --skip-download "https://www.bilibili.com/video/BV..."

# Download auto-generated subtitles as well
yt-dlp --write-sub --write-auto-sub --skip-download "https://www.bilibili.com/video/BV..."

# Download specific language (Chinese)
yt-dlp --write-sub --sub-lang zh-CN --skip-download "https://www.bilibili.com/video/BV..."

# Convert to SRT format
yt-dlp --write-sub --sub-lang zh-CN --convert-subs srt --skip-download "https://www.bilibili.com/video/BV..."
```

**Member-only videos (require cookies):**
```bash
yt-dlp --cookies-from-browser chrome --write-sub --skip-download "https://www.bilibili.com/video/BV..."
```

#### YouTube Subtitle Extraction

**List available subtitles:**
```bash
yt-dlp --list-subs "https://www.youtube.com/watch?v=VIDEO_ID"
```

**Download subtitles:**
```bash
# English subtitles
yt-dlp --write-sub --sub-lang en --skip-download "https://www.youtube.com/watch?v=VIDEO_ID"

# Auto-generated subtitles
yt-dlp --write-auto-sub --sub-lang en --skip-download "https://www.youtube.com/watch?v=VIDEO_ID"

# Multiple languages
yt-dlp --write-sub --sub-lang "en,zh-Hans,ja" --skip-download "URL"

# Convert to plain text (SRT format)
yt-dlp --write-auto-sub --sub-lang en --convert-subs srt --skip-download "URL"
```

### Step 3: Parse Subtitle Files

yt-dlp downloads subtitles in various formats. Here's how to parse common ones:

```python
import re
import json

def parse_srt(filepath: str) -> list[dict]:
    """Parse SRT subtitle file into structured segments."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    segments = []
    blocks = content.strip().split('\n\n')

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            time_match = re.match(
                r'(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})',
                lines[1]
            )
            if time_match:
                h, m, s = int(time_match[1]), int(time_match[2]), int(time_match[3])
                start_sec = h * 3600 + m * 60 + s
                text = ' '.join(lines[2:]).strip()
                text = re.sub(r'<[^>]+>', '', text)
                if text:
                    segments.append({
                        'start': start_sec,
                        'text': text
                    })

    return segments


def parse_json3(filepath: str) -> list[dict]:
    """Parse YouTube JSON3 subtitle format."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    segments = []
    for event in data.get('events', []):
        start_ms = event.get('tStartMs', 0)
        segs = event.get('segs', [])
        text = ''.join(s.get('utf8', '') for s in segs).strip()
        if text and text != '\n':
            segments.append({
                'start': start_ms / 1000,
                'text': text
            })

    return segments


def segments_to_text(segments: list[dict]) -> str:
    """Convert segments to plain text with timestamps."""
    lines = []
    for seg in segments:
        minutes = int(seg['start'] // 60)
        seconds = int(seg['start'] % 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg['text']}")
    return '\n'.join(lines)
```

### Step 4: Summarize or Query the Content

Once you have the transcript text, generate summaries or answer questions about the content.

**For summarization**, combine all subtitle text and apply a structured prompt:

```
Based on the following video transcript, provide:

1. **Executive Summary**: 2-3 sentences in the video's language
2. **Key Points**: Bulleted list with timestamps [MM:SS]
3. **Detailed Notes**: Organized by topic sections
4. **Q&A**: Answer any specific questions the user has

Transcript:
{full_transcript_text}
```

**For Q&A**, search the transcript for relevant segments first, then answer based on context.

---

## Workflows

### Workflow 1: Quick Bilibili Subtitle Extraction

User says: "Extract subtitles from this Bilibili video: https://www.bilibili.com/video/BV..."

1. Run `yt-dlp --list-subs` to check available subtitles
2. Download Chinese subtitles: `yt-dlp --write-sub --sub-lang zh-CN --convert-subs srt --skip-download URL`
3. Parse the SRT file
4. Present the clean transcript to the user

### Workflow 2: Bilibili Member Video

User says: "This is a member-only video, please extract subtitles for me"

1. Inform user that cookies are needed
2. Use `--cookies-from-browser chrome` (or user's preferred browser)
3. Extract subtitles with authentication
4. If cookies fail, guide user to export cookies.txt manually

### Workflow 3: YouTube Multi-Language

User says: "Get both English and Chinese subtitles from this YouTube video"

1. List available subtitle languages
2. Download both `en` and `zh-Hans` subtitles
3. Parse both files
4. Present side-by-side or merged view

### Workflow 4: Video Content Q&A

User says: "Does the video mention X?"

1. Extract full transcript
2. Search for keywords related to X
3. Return matching segments with timestamps
4. Provide a concise answer based on the matching content

### Workflow 5: Batch Playlist Extraction

User provides a playlist or series URL:

1. Use `yt-dlp --flat-playlist` to list all videos
2. Extract subtitles from each video sequentially
3. Save each transcript as a separate file
4. Generate a combined index with video titles and file paths

### Workflow 6: Bilibili Bangumi Subtitles

User shares a bangumi URL:

1. Bangumi episodes often have official multi-language subtitles
2. Use `--list-subs` to show all available languages
3. Download preferred language(s)
4. Note: Some bangumi require member cookies

---

## Output Format

### Transcript Output

```markdown
# Subtitles: [Video Title]

**Platform**: Bilibili / YouTube
**Language**: Chinese (zh-CN)
**Duration**: ~XX minutes
**Subtitle Type**: Manual / Auto-generated

---

[00:00] Hello everyone, welcome to today's video
[00:05] Today we're going to discuss...
[00:12] First, let's look at the background
...
```

### Summary Output

```markdown
# Video Summary: [Title]

## Executive Summary
[2-3 sentence summary in the video's language]

## Key Points
- **[00:00]** Opening introduction and topic overview
- **[02:15]** First core point
- **[08:30]** Key evidence and data
- **[15:00]** Practical demonstration
- **[22:45]** Wrap-up and next steps

## Detailed Notes

### Part 1: [Topic] (00:00 - 05:30)
[Detailed notes]

### Part 2: [Topic] (05:30 - 12:00)
[Detailed notes]
```

---

## Common Pitfalls

### 1. Bilibili Geo-Restrictions

**Problem**: Some Bilibili content is restricted to mainland China.

**Solutions**:
- Use a proxy or VPN with a Chinese IP: `yt-dlp --proxy socks5://127.0.0.1:1080 URL`
- Set the `--geo-bypass` flag: `yt-dlp --geo-bypass URL`
- For persistent issues, use `--geo-bypass-country CN`

```bash
yt-dlp --geo-bypass-country CN --write-sub --skip-download "URL"
```

### 2. Member-Only Content Without Cookies

**Problem**: `yt-dlp` returns an error or empty subtitles for member-only videos.

**Solution**: Always check if the video requires member access. If so, cookies are mandatory:

```bash
# If this fails:
yt-dlp --list-subs "URL"
# ERROR: This video requires premium membership

# Try with cookies:
yt-dlp --cookies-from-browser chrome --list-subs "URL"
```

If browser cookie extraction fails (common on Linux), export cookies manually to a `cookies.txt` file.

### 3. No Subtitles Available

**Problem**: Many Bilibili videos, especially older ones or user-generated content, have no subtitles at all.

**Solution**: Inform the user clearly. Unlike YouTube, Bilibili does not always generate auto-subtitles. The video may only have hardcoded (burned-in) subtitles which require OCR — beyond the scope of this skill.

---

## Pre-built Scripts

### scripts/extract_subs.py
Batch subtitle extraction for Bilibili and YouTube playlist videos.

```bash
python3 scripts/extract_subs.py --url "https://www.bilibili.com/video/BV..." --lang zh-CN
python3 scripts/extract_subs.py --url "https://www.youtube.com/playlist?list=..." --lang en
```

### 4. Incomplete Subtitle Languages

**Problem**: Some videos only have auto-generated subtitles in one language.

**Solution**: Download the available language and use LLM-powered translation to generate other languages.

---

## Advanced: Batch Processing

```python
import subprocess
import os

def batch_extract_subtitles(urls: list[str], platform: str = 'bilibili', lang: str = 'zh-CN'):
    """Batch extract subtitles from multiple videos."""
    results = []
    for url in urls:
        output_file = f"{os.path.basename(url)}_{lang}.srt"
        cmd = [
            'yt-dlp',
            '--write-sub', '--write-auto-sub',
            '--sub-lang', lang,
            '--convert-subs', 'srt',
            '--skip-download',
            '-o', output_file,
            url
        ]
        subprocess.run(cmd, check=True)
        results.append(output_file)
    return results
```
