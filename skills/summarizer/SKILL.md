---
name: openakita/skills@summarizer
description: Summarize content from any source — URLs, local files, YouTube videos, and raw text. Use when the user asks to summarize a webpage, PDF, document, article, video, or any content. Supports multiple output formats (bullet points, executive summary, detailed notes) and configurable length. Can also extract raw content without summarization.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
  based_on: moltbot/moltbot/summarize
---

# Universal Content Summarizer

Summarize content from any source: URLs, local files, YouTube videos, clipboard text, and more. Flexible output formats with configurable depth and style.

## When to Use This Skill

- User says "summarize this" and provides a URL, file, or text
- User shares a link to a webpage/article and wants a quick overview
- User has a PDF or document they want condensed
- User wants to extract content from a URL without summarizing (extract-only mode)
- User needs different summary formats for different audiences (executive vs. technical)
- User wants to summarize multiple sources and combine insights
- User asks for a TL;DR of any content

## Prerequisites

### Core Dependencies

No mandatory external dependencies for basic text summarization — the AI model handles it directly.

### For URL Content Extraction

The agent should use available web browsing/fetching tools to retrieve URL content. If running in an environment with shell access:

```bash
# For advanced HTML parsing (optional)
pip install beautifulsoup4 requests

# For PDF text extraction (optional)
pip install PyPDF2
# or
pip install pdfplumber
```

### For YouTube Videos

If the content source is a YouTube URL, this skill delegates to the youtube-summarizer or bilibili-watcher skills if available. Otherwise, it uses:

```bash
pip install youtube-transcript-api
```

### Supported Input Types

| Input Type | How to Provide | Notes |
|---|---|---|
| URL (webpage) | Paste the URL | HTML content extracted automatically |
| URL (YouTube) | Paste YouTube link | Transcript extracted via API |
| Local file (text) | File path | `.txt`, `.md`, `.rst`, `.csv` |
| Local file (PDF) | File path | Requires PyPDF2 or pdfplumber |
| Local file (HTML) | File path | Parsed with BeautifulSoup |
| Local file (DOCX) | File path | Requires python-docx |
| Raw text | Paste directly | Any length |
| Clipboard | "Summarize my clipboard" | If clipboard access available |

---

## Instructions

### Step 1: Identify the Content Source

Determine what the user wants summarized and how to access it:

```
Input Analysis:
1. Is it a URL? → Fetch the content
2. Is it a file path? → Read the file
3. Is it raw text? → Use directly
4. Is it a YouTube link? → Extract transcript
5. Is it multiple sources? → Process each, then combine
```

**URL Detection Patterns:**

```python
import re

def classify_input(text: str) -> str:
    """Classify the input type."""
    text = text.strip()

    # YouTube URLs
    youtube_pattern = r'(youtube\.com|youtu\.be|youtube\.com/shorts)'
    if re.search(youtube_pattern, text):
        return 'youtube'

    # Bilibili URLs
    if 'bilibili.com' in text or 'b23.tv' in text:
        return 'bilibili'

    # General URLs
    if re.match(r'https?://', text):
        return 'url'

    # File paths
    if any(text.endswith(ext) for ext in ['.pdf', '.txt', '.md', '.html', '.docx', '.rst', '.csv']):
        return 'file'

    # Raw text
    return 'text'
```

### Step 2: Extract Content

#### From URLs (Webpages)

Use the available web fetching tools to retrieve and parse HTML content. Extract the main article text, removing navigation, ads, footers, and other boilerplate.

**Key extraction goals:**
- Article title and author
- Publication date if available
- Main body text with structure preserved
- Images and captions (noted but not downloaded)
- Any embedded data tables

```python
from bs4 import BeautifulSoup
import requests

def extract_url_content(url: str) -> dict:
    """Extract main content from a URL."""
    response = requests.get(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; ContentSummarizer/1.0)'
    }, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    # Remove script, style, nav, footer elements
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()

    # Try to find the main article content
    article = soup.find('article') or soup.find('main') or soup.find('body')

    title = soup.find('title')
    title_text = title.get_text().strip() if title else 'Untitled'

    return {
        'title': title_text,
        'text': article.get_text(separator='\n', strip=True) if article else '',
        'url': url
    }
```

#### From Local Files

```python
from pathlib import Path

def extract_file_content(filepath: str) -> dict:
    """Extract text from various file formats."""
    path = Path(filepath)
    suffix = path.suffix.lower()

    if suffix in ('.txt', '.md', '.rst', '.csv'):
        text = path.read_text(encoding='utf-8')
        return {'title': path.name, 'text': text, 'format': suffix}

    elif suffix == '.pdf':
        return extract_pdf(filepath)

    elif suffix == '.html':
        text = path.read_text(encoding='utf-8')
        soup = BeautifulSoup(text, 'html.parser')
        for tag in soup(['script', 'style']):
            tag.decompose()
        return {
            'title': path.name,
            'text': soup.get_text(separator='\n', strip=True),
            'format': 'html'
        }

    elif suffix == '.docx':
        return extract_docx(filepath)

    else:
        # Try reading as plain text
        try:
            text = path.read_text(encoding='utf-8')
            return {'title': path.name, 'text': text, 'format': 'unknown'}
        except UnicodeDecodeError:
            raise ValueError(f"Cannot read binary file: {filepath}")


def extract_pdf(filepath: str) -> dict:
    """Extract text from PDF using available libraries."""
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            pages = [page.extract_text() or '' for page in pdf.pages]
            return {
                'title': Path(filepath).name,
                'text': '\n\n'.join(pages),
                'format': 'pdf',
                'pages': len(pdf.pages)
            }
    except ImportError:
        pass

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        pages = [page.extract_text() or '' for page in reader.pages]
        return {
            'title': Path(filepath).name,
            'text': '\n\n'.join(pages),
            'format': 'pdf',
            'pages': len(reader.pages)
        }
    except ImportError:
        raise RuntimeError("Install pdfplumber or PyPDF2 to read PDFs: pip install pdfplumber")


def extract_docx(filepath: str) -> dict:
    """Extract text from DOCX files."""
    try:
        from docx import Document
        doc = Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return {
            'title': Path(filepath).name,
            'text': '\n\n'.join(paragraphs),
            'format': 'docx'
        }
    except ImportError:
        raise RuntimeError("Install python-docx to read DOCX files: pip install python-docx")
```

#### From YouTube Videos

Delegate to the youtube-summarizer skill or use youtube-transcript-api directly:

```python
from youtube_transcript_api import YouTubeTranscriptApi

def extract_youtube_content(url: str) -> dict:
    """Extract transcript from YouTube video."""
    video_id = extract_video_id(url)  # See youtube-summarizer skill
    transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'zh-Hans', 'ja'])
    text = ' '.join(entry['text'] for entry in transcript)
    return {
        'title': f'YouTube Video {video_id}',
        'text': text,
        'format': 'youtube',
        'segments': transcript
    }
```

### Step 3: Generate the Summary

Choose the output format based on user request or default to bullet points.

---

## Output Formats

### Format 1: Bullet Points (Default)

Best for: Quick scanning, team sharing, Slack/email updates.

```
# Summary: [Title]

**Source**: [URL or filename]
**Length**: ~X words / X pages / X minutes

## Key Points
• [Most important finding/conclusion]
• [Second key point]
• [Third key point]
• [Fourth key point — include specific numbers/data if available]
• [Fifth key point]

## Notable Details
• [Interesting data point or quote]
• [Counter-argument or limitation mentioned]
```

**Prompt template:**
```
Summarize the following content into 5-8 bullet points. Each bullet should:
- Be self-contained (understandable without reading the full text)
- Include specific numbers, names, or dates when relevant
- Be ordered by importance (most important first)
- Be concise (1-2 sentences max)

Content:
{content}
```

### Format 2: Executive Summary

Best for: Leadership updates, decision-making, meeting prep.

```
# Executive Summary: [Title]

**Source**: [URL/file] | **Date**: [if available] | **Read time**: ~X min

## Bottom Line
[1-2 sentences: the single most important takeaway]

## Context
[2-3 sentences: why this matters, background]

## Key Findings
1. [Finding with supporting data]
2. [Finding with supporting data]
3. [Finding with supporting data]

## Implications
[What this means for the reader/team/organization]

## Recommended Actions
1. [Action item]
2. [Action item]
```

**Prompt template:**
```
Write an executive summary of the following content. Target audience: busy decision-makers
who need to understand the core message in under 2 minutes.

Structure:
1. Bottom Line (1-2 sentences — what's the one thing they need to know?)
2. Context (2-3 sentences — why does this matter?)
3. Key Findings (3-5 numbered points with data)
4. Implications (what this means going forward)
5. Recommended Actions (concrete next steps)

Content:
{content}
```

### Format 3: Detailed Notes

Best for: Research, studying, reference material.

```
# Detailed Notes: [Title]

**Source**: [URL/file]
**Summary date**: [today]
**Original length**: ~X words

## Overview
[3-5 sentence comprehensive overview]

## Section 1: [Topic]
[Detailed notes preserving key information, quotes, data]
- Sub-point with specifics
- Sub-point with specifics

## Section 2: [Topic]
[Detailed notes]

## Section 3: [Topic]
[Detailed notes]

## Key Quotes
> "[Exact quote]" — [Source/Author]
> "[Exact quote]" — [Source/Author]

## Data & Statistics
| Metric | Value | Context |
|---|---|---|
| [metric] | [value] | [context] |

## References & Links
- [Reference mentioned in the content]
```

### Format 4: Extract Only (No Summarization)

Best for: Content extraction for downstream processing.

When the user says "just extract" or "don't summarize", return the raw extracted text in clean markdown format without any summarization or analysis:

```
# Extracted Content: [Title]

**Source**: [URL/file]
**Extracted**: [timestamp]
**Word count**: X

---

[Full extracted text in clean markdown]
```

---

## Workflows

### Workflow 1: Quick URL Summary

User says: "Summarize https://example.com/article"

1. Detect input type: URL
2. Fetch and parse the webpage content
3. Generate bullet-point summary (default format)
4. Present with source attribution

### Workflow 2: PDF Summary

User says: "Summarize this PDF: /path/to/document.pdf"

1. Detect input type: file (PDF)
2. Extract text from all pages
3. Note total page count
4. Generate summary in requested format
5. Flag any extraction issues (scanned PDFs, images, etc.)

### Workflow 3: Custom Format Summary

User says: "Give me an executive summary of this article"

1. Detect input type and extract content
2. Use executive summary format
3. Include bottom line, key findings, and action items

### Workflow 4: Multi-Source Synthesis

User provides multiple URLs/files:

1. Extract content from each source
2. Summarize each independently
3. Create a synthesis section highlighting:
   - Common themes across sources
   - Contradictions or differing perspectives
   - Unique insights from each source
4. Present combined analysis

### Workflow 5: Configurable Length

User says: "Give me a 3-sentence summary" or "detailed 2000-word summary"

1. Extract content
2. Adjust summary length based on user specification:
   - "brief" / "TL;DR" → 2-3 sentences
   - "short" → 5-8 bullet points
   - "medium" (default) → Full structured summary
   - "detailed" / "comprehensive" → Detailed notes format with all specifics

### Workflow 6: Content Extraction Only

User says: "Just extract the text from this URL, don't summarize"

1. Fetch and parse the content
2. Clean up HTML/formatting artifacts
3. Return raw text in clean markdown
4. No summarization applied

### Workflow 7: YouTube/Video Summary

User shares a YouTube or Bilibili link:

1. Detect as video URL
2. Extract transcript (delegate to youtube-summarizer or bilibili-watcher if available)
3. Summarize transcript with timestamps
4. Format output appropriate to video content

---

## Configurable Options

When processing a summarization request, consider these adjustable parameters:

| Parameter | Options | Default |
|---|---|---|
| **Format** | bullet, executive, detailed, extract-only | bullet |
| **Length** | brief, short, medium, detailed | medium |
| **Language** | Output language code | Same as source |
| **Focus** | Specific topic/aspect to emphasize | None (general) |
| **Audience** | technical, general, executive, academic | general |
| **Include quotes** | yes/no | yes for detailed |
| **Include data** | yes/no | yes |
| **Max points** | Number of bullet points | 8 |

Users can specify these naturally:
- "Summarize in Chinese" → language: zh
- "Technical summary for engineers" → audience: technical
- "Just the top 3 points" → max_points: 3, length: brief

---

## Common Pitfalls

### 1. Paywalled or Login-Required Content

**Problem**: Many news sites and platforms require subscriptions or login.

**Solutions**:
- Try the URL first; many sites allow limited free access
- Check for cached versions or alternative URLs
- Inform the user if content is inaccessible and suggest alternatives
- Never attempt to bypass paywalls

### 2. JavaScript-Rendered Content

**Problem**: Some pages load content dynamically via JavaScript, making simple HTTP requests return empty shells.

**Solutions**:
- Use browser-based fetching tools when available
- Try adding `?format=text` or similar URL parameters
- Look for RSS feeds or API endpoints that serve the same content
- For SPAs, check if there's a server-rendered version

### 3. Very Long Content

**Problem**: Documents over 50,000 words may exceed model context limits.

**Solutions**:
- For PDFs: summarize page-by-page or chapter-by-chapter, then combine
- For webpages: extract only the main article content, skip comments and sidebars
- Use chunked processing:

```python
def chunk_text(text: str, max_chars: int = 30000) -> list[str]:
    """Split text into manageable chunks at paragraph boundaries."""
    paragraphs = text.split('\n\n')
    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > max_chars and current:
            chunks.append('\n\n'.join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para)

    if current:
        chunks.append('\n\n'.join(current))

    return chunks
```

### 4. Non-Text Content

**Problem**: User provides a file that's primarily images, charts, or scanned documents.

**Solutions**:
- For scanned PDFs: inform user that OCR is needed (beyond basic scope)
- For image-heavy articles: note that visual content is not captured in the summary
- Suggest tools like Tesseract for OCR if needed

### 5. Encoding Issues

**Problem**: Files with unusual encodings (GB2312, Shift-JIS, etc.) may not parse correctly.

**Solutions**:
- Try common encodings in order: UTF-8, UTF-16, GB2312, GBK, Shift-JIS, Latin-1
- Use `chardet` library for automatic detection if available

```python
def read_with_fallback(filepath: str) -> str:
    """Read file trying multiple encodings."""
    encodings = ['utf-8', 'utf-8-sig', 'gb2312', 'gbk', 'gb18030', 'shift-jis', 'latin-1']
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Cannot decode {filepath} with any known encoding")
```

### 6. Summarization Quality

**Problem**: Summaries may miss nuance, oversimplify, or hallucinate details.

**Solutions**:
- Always attribute the summary to the source
- For critical use cases, recommend the user verify key claims
- When uncertain about content interpretation, flag it explicitly
- Preserve specific numbers, dates, and names rather than generalizing

### 7. Rate Limits on URL Fetching

**Problem**: Fetching many URLs quickly may trigger rate limits or blocks.

**Solutions**:
- Add delays between requests (1-2 seconds)
- Respect robots.txt directives
- Use appropriate User-Agent headers
- Cache fetched content to avoid re-fetching

---

## Multi-AI Model Support

This skill works with any AI model capable of text summarization. The prompts and workflows are model-agnostic. For best results:

| Model Capability | Recommended Use |
|---|---|
| Large context window (100K+) | Full document summarization in one pass |
| Standard context (8K-32K) | Chunked processing with merge step |
| Fast inference | Batch processing of multiple sources |
| Multi-language | Cross-language summary generation |

The skill automatically adapts to the available model's capabilities:
- For large context models: send full content in one request
- For smaller context models: chunk, summarize each, then synthesize
- For multi-modal models: include image descriptions when available

