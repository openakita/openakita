---
name: openakita/skills@wechat-article
description: Create and format WeChat Official Account articles with proper Markdown-to-WeChat HTML conversion, rich formatting, cover image guidance, and both API and manual publishing workflows.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# WeChat Official Account Publishing Assistant

A skill designed for WeChat Official Account content creation and publishing, providing end-to-end support from writing to formatting to publishing, including Markdown conversion, rich text styles, cover design, and summary generation.

## When to Use

- Writing Official Account original articles (deep long-form, opinion commentary, industry analysis)
- Converting Markdown articles to WeChat Official Account format
- Article layout beautification and style optimization
- Cover image selection and summary generation
- Multi-platform content adaptation (converting blog/Zhihu content to Official Account format)
- Batch article publishing via API
- Manual copy-paste publish formatting optimization

## Markdown to WeChat HTML Conversion

### Core Conversion Principle

WeChat Official Account editor does not support standard Markdown. Markdown must be converted to inline-styled HTML. Key limitations:

- **Does not support** `<style>` tags or external CSS
- **Does not support** `class` attributes
- **All styles must be inline** (`style="..."` attribute)
- Images must be uploaded to the WeChat media library (external links will not display)
- No JavaScript support

### Basic Element Conversion Table

| Markdown | WeChat HTML |
|----------|----------|
| `# Title` | `<h1 style="font-size:22px;font-weight:bold;color:#333;border-bottom:2px solid #f0b849;padding-bottom:8px;">Title</h1>` |
| `## Title` | `<h2 style="font-size:18px;font-weight:bold;color:#333;margin-top:24px;">Title</h2>` |
| `**Bold**` | `<strong style="color:#f0b849;">Bold</strong>` |
| `> Quote` | Use golden quote box (see below) |
| `` `code` `` | `<code style="background:#f5f5f5;padding:2px 6px;border-radius:3px;font-size:14px;color:#c7254e;">code</code>` |
| `---` | `<hr style="border:none;border-top:1px dashed #ddd;margin:24px 0;">` |
| `[link](url)` | `<a style="color:#576b95;text-decoration:none;">link text</a>` (WeChat doesn't support external links, only for "read original") |

### Golden Quote Box (Signature Style)

```html
<blockquote style="
  border-left: 4px solid #f0b849;
  background: linear-gradient(to right, #fdf8e8, #ffffff);
  padding: 16px 20px;
  margin: 16px 0;
  border-radius: 0 8px 8px 0;
  font-size: 15px;
  color: #666;
  line-height: 1.8;
">
  Quote content
</blockquote>
```

### Paragraph Dividers

```html
<!-- Minimal divider -->
<p style="text-align:center;color:#f0b849;font-size:16px;margin:20px 0;">
  ✦ ✦ ✦
</p>

<!-- Dashed divider -->
<hr style="border:none;border-top:1px dashed #e0e0e0;margin:24px 0;">

<!-- Graphic divider -->
<p style="text-align:center;color:#ccc;font-size:12px;letter-spacing:8px;margin:24px 0;">
  ◆◇◆◇◆
</p>
```

## Official Account Formatting Standards

### 1. Body Text Formatting

**Font and spacing:**
```html
<section style="
  font-size: 15px;
  color: #3f3f3f;
  line-height: 1.8;
  letter-spacing: 0.5px;
  word-spacing: 2px;
  text-align: justify;
  padding: 0 8px;
">
  Body content
</section>
```

**Core formatting parameters:**

| Parameter | Recommended Value | Description |
|------|--------|------|
| Font size | 15px | Best for mobile reading |
| Line height | 1.8 | Optimal reading comfort |
| Letter spacing | 0.5px | Adds breathing room |
| Paragraph spacing | 16px | Space between paragraphs |
| Side margins | 8px | Content inset |
| Body color | #3f3f3f | Dark gray, easy on the eyes |
| Accent color | #f0b849 | Gold, brand feel |

### 2. Heading Styles

**Level 1 Heading (Article Title):**
```html
<h1 style="
  font-size: 22px;
  font-weight: bold;
  color: #1a1a1a;
  text-align: center;
  margin: 32px 0 16px;
  line-height: 1.4;
">Article Title</h1>
```

**Level 2 Heading (Chapter):**
```html
<h2 style="
  font-size: 18px;
  font-weight: bold;
  color: #333;
  border-left: 4px solid #f0b849;
  padding-left: 12px;
  margin: 28px 0 12px;
  line-height: 1.5;
">Chapter Title</h2>
```

**Level 3 Heading (Section):**
```html
<h3 style="
  font-size: 16px;
  font-weight: bold;
  color: #555;
  margin: 20px 0 8px;
  line-height: 1.5;
">Section Title</h3>
```

### 3. Special Formatting Components

**Highlight Box:**
```html
<section style="
  background: #fffbeb;
  border: 1px solid #f0b849;
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
">
  <p style="font-weight:bold;color:#f0a000;margin-bottom:8px;">💡 Tip</p>
  <p style="font-size:14px;color:#666;line-height:1.6;">Tip content</p>
</section>
```

**Numbered List (Styled):**
```html
<section style="margin:12px 0;display:flex;align-items:flex-start;">
  <span style="
    display:inline-block;
    width:24px;height:24px;
    background:#f0b849;
    color:#fff;
    border-radius:50%;
    text-align:center;
    line-height:24px;
    font-size:13px;
    font-weight:bold;
    margin-right:12px;
    flex-shrink:0;
  ">1</span>
  <span style="font-size:15px;color:#3f3f3f;line-height:1.8;">List item content</span>
</section>
```

**Image Caption:**
```html
<figure style="text-align:center;margin:20px 0;">
  <img src="[image_url]" style="max-width:100%;border-radius:8px;">
  <figcaption style="
    font-size:12px;
    color:#999;
    margin-top:8px;
    line-height:1.5;
  ">Image caption</figcaption>
</figure>
```

## Cover Image Design

### Size Specifications

| Type | Size | Ratio | Usage |
|------|------|------|------|
| Primary cover | 900×383 px | 2.35:1 | First article |
| Secondary cover | 500×500 px | 1:1 | Second article and beyond |
| Article header image | 1080×608 px | 16:9 | Article top banner |

### Cover Design Principles

1. **Clear text**: Title font at least 40px, readable in thumbnail
2. **Eye-catching colors**: High contrast, avoid pure white backgrounds
3. **Clear theme**: Instantly conveys article topic
4. **Unified style**: Build account visual identity system
5. **Adequate white space**: Avoid information overload

### Cover Text Layout Formula

```
Main title (large) + Subtitle/keyword (small) + Brand mark (corner)
```

## Summary Generation

WeChat summaries appear in push messages and share cards, max **120 characters**.

**Summary writing principles:**
- First sentence captures core article value
- Include 1-2 keywords (SEO)
- Create suspense or offer a benefit point
- Avoid repeating the title

**Template:**
```
[Core insight/discovery]. This article analyzes from [angle1] and [angle2] perspectives,
and provides [specific value]. Suitable for [target readers].
```

## Publishing Workflow

### Method 1: API Auto-Publish (Recommended)

Suitable for users with WeChat Official Account developer permissions.

```
Step 1: Markdown article → WeChat HTML conversion
  ↓
Step 2: Upload images to WeChat media library
  - Call material management API to upload images
  - Get media_id and replace image links
  ↓
Step 3: Generate cover image + summary
  ↓
Step 4: Create draft
  - POST /cgi-bin/draft/add
  - Parameters: title, content, digest, thumb_media_id
  ↓
Step 5: Preview and verify
  - POST /cgi-bin/message/mass/preview
  - Send to specified account for preview
  ↓
Step 6: Mass send or schedule publish
  - POST /cgi-bin/freepublish/submit
```

**Key API Endpoints:**

| Endpoint | Purpose |
|------|------|
| `POST /cgi-bin/material/add_material` | Upload permanent media |
| `POST /cgi-bin/media/uploadimg` | Upload article images |
| `POST /cgi-bin/draft/add` | Create new draft |
| `POST /cgi-bin/draft/update` | Update draft |
| `POST /cgi-bin/freepublish/submit` | Publish |
| `POST /cgi-bin/message/mass/preview` | Preview |

### Method 2: Manual Publish

Suitable for users without API permissions or occasional publishing.

```
Step 1: Generate WeChat HTML content
  ↓
Step 2: Copy HTML to Official Account editor
  - Open mp.weixin.qq.com → Content Management → New Article
  - Switch to "Code Edit" mode (</> button)
  - Paste HTML code
  ↓
Step 3: Switch back to "Visual Edit" mode to check layout
  - Confirm heading styles are correct
  - Confirm images display properly
  - Confirm quote boxes and dividers render correctly
  ↓
Step 4: Upload cover image, fill summary
  ↓
Step 5: Preview → Check on mobile → Publish
```

**Manual publishing notes:**
- Switching to visual mode may alter some inline styles — always re-check
- WeChat will automatically compress uploaded images — use compressed images beforehand
- Do not add external links in the article content (WeChat will filter them)
- Cover images must meet size specifications or will be cropped unexpectedly