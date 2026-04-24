---
name: openakita/skills@youtube-summarizer
description: Summarize YouTube videos by extracting transcripts and generating structured notes. Use when the user wants to summarize a YouTube video, extract key points from a talk, create study notes from a lecture, or get timestamps for important moments. Supports multiple URL formats and languages.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
  based_on: sickn33/antigravity-awesome-skills/youtube-summarizer
---

# YouTube Video Summarizer

Extract transcripts from YouTube videos and generate structured, actionable summaries using the **STAR + R-I-S-E** framework.

## When to Use This Skill

- User shares a YouTube link and asks "summarize this" or "what's this video about"
- User wants key takeaways or study notes from a lecture/talk
- User needs timestamped highlights from a long video
- User wants a structured breakdown of a tutorial or presentation
- User asks to compare content across multiple YouTube videos
- User wants to extract quotes or specific segments from a video

## Prerequisites

### Install Required Package

```bash
pip install youtube-transcript-api
```

If pip is not available, try:

```bash
pip3 install youtube-transcript-api
```

Verify installation:

```python
from youtube_transcript_api import YouTubeTranscriptApi
print("youtube-transcript-api is ready")
```

### Supported Python Versions

- Python 3.8+
- No additional system dependencies required

---

## Instructions

### Step 1: Extract the Video ID

YouTube URLs come in many formats. Parse all of them to extract the 11-character video ID:

| URL Format | Example | Extraction |
|---|---|---|
| Standard | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | Query param `v` |
| Short | `https://youtu.be/dQw4w9WgXcQ` | Path segment |
| Embed | `https://www.youtube.com/embed/dQw4w9WgXcQ` | Path after `/embed/` |
| Live | `https://www.youtube.com/live/dQw4w9WgXcQ` | Path after `/live/` |
| Shorts | `https://www.youtube.com/shorts/dQw4w9WgXcQ` | Path after `/shorts/` |
| With timestamp | `https://youtu.be/dQw4w9WgXcQ?t=120` | Ignore query params |
| With playlist | `https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxx` | Use only `v` param |
| Mobile | `https://m.youtube.com/watch?v=dQw4w9WgXcQ` | Same as standard |
| Bare ID | `dQw4w9WgXcQ` | Use directly |

```python
import re
from urllib.parse import urlparse, parse_qs

def extract_video_id(url_or_id: str) -> str:
    """Extract YouTube video ID from various URL formats."""
    url_or_id = url_or_id.strip()

    # Bare video ID (11 chars, alphanumeric + dash + underscore)
    if re.match(r'^[A-Za-z0-9_-]{11}$', url_or_id):
        return url_or_id

    parsed = urlparse(url_or_id)

    # youtu.be short links
    if parsed.hostname in ('youtu.be',):
        return parsed.path.lstrip('/')

    # Standard, mobile, embed, live, shorts
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            qs = parse_qs(parsed.query)
            return qs.get('v', [None])[0]
        for prefix in ('/embed/', '/live/', '/shorts/', '/v/'):
            if parsed.path.startswith(prefix):
                return parsed.path[len(prefix):].split('/')[0]

    raise ValueError(f"Cannot extract video ID from: {url_or_id}")
```

### Step 2: Fetch the Transcript

```python
from youtube_transcript_api import YouTubeTranscriptApi

def fetch_transcript(video_id: str, preferred_langs=None):
    """
    Fetch transcript with language fallback.

    Args:
        video_id: The 11-character YouTube video ID
        preferred_langs: Ordered list of language codes, e.g. ['zh-Hans', 'zh', 'en']
                        Defaults to ['en'] if not specified

    Returns:
        List of transcript segments with 'text', 'start', 'duration' keys
    """
    if preferred_langs is None:
        preferred_langs = ['en']

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Try manually created transcripts first (more accurate)
        for lang in preferred_langs:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                return transcript.fetch()
            except Exception:
                continue

        # Fall back to auto-generated transcripts
        for lang in preferred_langs:
            try:
                transcript = transcript_list.find_generated_transcript([lang])
                return transcript.fetch()
            except Exception:
                continue

        # Last resort: get any available transcript and translate
        for transcript in transcript_list:
            try:
                if 'en' in preferred_langs:
                    return transcript.translate('en').fetch()
                return transcript.fetch()
            except Exception:
                continue

        raise RuntimeError("No transcript available in any language")

    except Exception as e:
        raise RuntimeError(f"Failed to fetch transcript for {video_id}: {e}")
```

### Step 3: Generate the Summary

Use the **STAR + R-I-S-E** combined framework for maximum clarity:

#### STAR Framework (for factual/process content)

- **S**ituation — What context does the video establish?
- **T**ask — What problem or question is being addressed?
- **A**ction — What steps, methods, or arguments are presented?
- **R**esult — What conclusions, outcomes, or calls to action are given?

#### R-I-S-E Framework (for opinion/analysis content)

- **R**eference — What sources, data, or authorities are cited?
- **I**nsight — What unique perspectives or non-obvious points are made?
- **S**ummary — What are the essential takeaways?
- **E**valuation — What are the strengths and weaknesses of the arguments?

### Prompt Template for Summary Generation

When generating the summary from the transcript, use this structure:

```
You are summarizing a YouTube video transcript. Follow these rules strictly:

1. TITLE & METADATA
   - Video title (if known)
   - Estimated duration from timestamps
   - Primary topic/category

2. EXECUTIVE SUMMARY (2-3 sentences)
   - The single most important takeaway
   - Who should watch this and why

3. KEY POINTS (bulleted, with timestamps)
   - [MM:SS] Point description
   - Maximum 10 key points
   - Each point should be self-contained and actionable

4. DETAILED NOTES (organized by topic/section)
   - Group related content under headers
   - Include specific examples, numbers, quotes
   - Preserve technical accuracy

5. STAR ANALYSIS
   - Situation: ...
   - Task: ...
   - Action: ...
   - Result: ...

6. ACTIONABLE TAKEAWAYS
   - What the viewer should do after watching
   - Resources mentioned in the video
```

---

## Workflows

### Workflow 1: Quick Summary

User says: "Summarize this video: https://youtube.com/watch?v=..."

1. Extract video ID from URL
2. Fetch English transcript (fall back to auto-generated)
3. Generate executive summary + key points with timestamps
4. Present in clean markdown format

### Workflow 2: Deep Dive Notes

User says: "I need detailed study notes from this lecture"

1. Extract video ID
2. Fetch transcript in preferred language
3. Generate full STAR + R-I-S-E analysis
4. Include all timestamps, quotes, and specific data points
5. Add a "Review Questions" section at the end

### Workflow 3: Multi-Language Summary

User says: "Summarize this Chinese/Japanese/Korean video in English"

1. Extract video ID
2. List available transcript languages
3. Fetch best available transcript
4. Translate content if needed (use transcript API's built-in translation)
5. Generate summary in the user's preferred language

### Workflow 4: Batch Processing

User provides multiple URLs:

1. Extract all video IDs
2. Fetch transcripts sequentially (respect rate limits)
3. Generate individual summaries
4. Create a comparison table if videos are related
5. Highlight common themes and unique points

### Workflow 5: Timestamp Navigation

User says: "Find where they talk about X in this video"

1. Fetch full transcript
2. Search for relevant keywords/concepts
3. Return matching segments with precise timestamps
4. Format as clickable timestamp links: `[MM:SS](https://youtu.be/ID?t=SECONDS)`

---

## Output Format

### Standard Summary Output

```markdown
# 📺 Video Summary: [Title]

**Duration**: ~XX minutes | **Language**: English | **Category**: Technology

## Executive Summary
[2-3 sentence overview]

## Key Points
- **[0:00]** Introduction and context setting
- **[2:15]** First major point with supporting evidence
- **[8:30]** Key technical concept explained
- **[15:00]** Practical demonstration
- **[22:45]** Conclusion and call to action

## Detailed Notes

### Section 1: [Topic] (0:00 - 5:30)
[Detailed notes with specific information]

### Section 2: [Topic] (5:30 - 12:00)
[Detailed notes with specific information]

## STAR Analysis
- **Situation**: [Context]
- **Task**: [Problem/Question]
- **Action**: [Methods/Steps]
- **Result**: [Outcomes/Conclusions]

## Actionable Takeaways
1. [First action item]
2. [Second action item]
3. [Third action item]

## Resources Mentioned
- [Resource 1](url)
- [Resource 2](url)
```

---

## Common Pitfalls

### 1. Transcript Unavailable

**Problem**: Some videos have no transcript (auto-generated or manual).

**Solutions**:
- Check if the video is a music video, live stream, or very short clip — these often lack transcripts
- Try different language codes: `['en', 'en-US', 'en-GB']`
- If auto-generated is disabled by the uploader, there's no workaround via this API

**Error handling**:
```python
try:
    transcript = fetch_transcript(video_id)
except RuntimeError as e:
    if "disabled" in str(e).lower():
        print("Subtitles are disabled for this video by the uploader.")
    elif "no transcript" in str(e).lower():
        print("No transcript available. Try a different video.")
    else:
        print(f"Unexpected error: {e}")
```

### 2. Age-Restricted or Private Videos

**Problem**: Age-restricted videos require authentication; private videos are inaccessible.

**Solution**: Inform the user that these videos cannot be processed without authentication cookies. The `youtube-transcript-api` does not support cookie-based auth natively.

### 3. Rate Limiting

**Problem**: Fetching many transcripts in quick succession may trigger rate limits.

**Solutions**:
- Add a 1-2 second delay between requests when batch processing
- Cache transcripts locally to avoid re-fetching
- Handle HTTP 429 errors with exponential backoff

```python
import time

def fetch_with_retry(video_id, max_retries=3):
    for attempt in range(max_retries):
        try:
            return fetch_transcript(video_id)
        except Exception as e:
            if '429' in str(e) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
                continue
            raise
```

### 4. Transcript Quality Issues

**Problem**: Auto-generated transcripts may contain errors, especially for technical terms, proper nouns, or non-English content.

**Solutions**:
- Prefer manually created transcripts when available
- Flag to the user that auto-generated transcripts may have inaccuracies
- Use context clues to correct obvious errors in the summary

### 5. Very Long Videos

**Problem**: Videos over 2 hours may produce extremely long transcripts that exceed context limits.

**Solutions**:
- Chunk the transcript into segments (e.g., 15-minute blocks)
- Summarize each chunk independently, then create an overall summary
- Let the user specify a time range to focus on

```python
def chunk_transcript(transcript, chunk_minutes=15):
    """Split transcript into time-based chunks."""
    chunks = []
    current_chunk = []
    chunk_end = chunk_minutes * 60

    for segment in transcript:
        if segment['start'] >= chunk_end and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            chunk_end += chunk_minutes * 60
        current_chunk.append(segment)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks
```

### 6. Invalid or Expired URLs

**Problem**: Shortened URLs, expired links, or non-YouTube URLs.

**Solution**: Validate the extracted video ID format (11 characters, `[A-Za-z0-9_-]`) before making API calls. Return a clear error message for invalid inputs.

---

## Advanced: Combining with Video Metadata

For richer summaries, consider fetching video metadata (title, channel, description) using the YouTube Data API v3 or by parsing the video page. This adds context but requires either an API key or HTML parsing.

The transcript-only approach used here is intentionally lightweight — no API key needed, no OAuth, minimal dependencies.

---

## Language Support

The `youtube-transcript-api` supports all languages that YouTube provides transcripts for. Common language codes:

| Language | Code | Auto-generated |
|---|---|---|
| English | `en` | Yes |
| Chinese (Simplified) | `zh-Hans` | Yes |
| Chinese (Traditional) | `zh-Hant` | Yes |
| Japanese | `ja` | Yes |
| Korean | `ko` | Yes |
| Spanish | `es` | Yes |
| French | `fr` | Yes |
| German | `de` | Yes |
| Portuguese | `pt` | Yes |
| Russian | `ru` | Yes |

To get all available languages for a video:

```python
transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
for t in transcript_list:
    print(f"{t.language} ({t.language_code}) - {'manual' if not t.is_generated else 'auto'}")
```

