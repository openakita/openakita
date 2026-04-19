---
name: openakita/skills@xiaohongshu-creator
description: Create engaging Xiaohongshu (RED/Xiaohongshu) content including titles, body text, hashtags, and image style recommendations. Supports multiple content types such as product reviews, tutorials, lifestyle sharing, and shopping guides with platform-specific optimization.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Xiaohongshu Content Creation Assistant

A content creation skill designed for the Xiaohongshu (RED) platform, helping you generate high-quality notes that match the platform's tone, covering titles, body text, hashtag recommendations, and image suggestions.

## When to Use

- Writing recommendation notes (product recommendations, shopping shares)
- Writing product review notes
- Writing tutorial notes (beauty, fashion, cooking, DIY)
- Writing lifestyle sharing notes (travel, daily life, check-ins)
- Brand collaboration content creation
- Xiaohongshu account operation content planning
- Batch generation of note frameworks

## Core Creation Guidelines

### 1. Title Rules

The title is the first impression of a note and directly determines its click-through rate.

**Hard requirements:**
- Character limit: **no more than 20 characters**
- Emoji count: **1-2**, placed at the beginning or end of the title
- Prohibited: Stacked exclamation marks (`!!!`), ALL CAPS

**Hook elements (use at least 1):**

| Hook type | Description | Example |
|----------|------|------|
| Number method | Create a sense of specificity with numbers | `3 steps to nail your commute makeup look 🌟` |
| Contrast method | Create surprise | `Earning 3K but looking like you earn 30K ✨` |
| Pain point method | Target audience pain points directly | `Holy grail shades for warm skin tones 🎨` |
| Suspense method | Spark curiosity | `This habit helped me lose 20 jin 💪` |
| Comparison method | Before/after or A/B comparison | `Changes after a month of morning C & night A 🔥` |
| Authority method | Leverage professional backing | `Dermatologist-recommended moisturizer 💊` |
| Scene method | Specific usage scenario | `30-minute emergency makeup before a date 💄` |
| Resonance method | Emotional resonance | `Efficient breakfast plans for office workers ☀️` |

**Title templates:**
```
[Number] + [Core keyword] + [Benefit] + [emoji]
[Identity label] + [Action] + [Result] + [emoji]
[Scenario] + [Solution] + [emoji]
```

### 2. Body Structure

Body text should be **300-500 characters**, following a four-part structure:

```
🔥 Hook (opening hook)  — 1-2 sentences, grab attention
📝 Core (core content)  — main information, valuable substance
📌 Summary (summary)    — concise key points
👉 CTA (call to action) — encourage engagement
```

**Hook writing styles:**
- Question style: `Have you ever had this problem?`
- Resonance style: `Every office worker needs this!`
- Suspense style: `After three years of trying, I finally found the best...`
- Achievement style: `After 30 days, the results were amazing`

**Core writing guidelines:**
- Use emojis (📍🔸💡 etc.) instead of plain text lists
- Keep each point to 1-2 lines
- Intersperse personal experiences and feelings (adds authenticity)
- Highlight important information with[]or""
- Use line breaks appropriately, avoid walls of text

**Common CTA phrases:**
- `If you found this useful, remember to like and save~`
- `What kind of content do you want to see next? Let me know in the comments`
- `Any fellow sisters with the same experience? Raise your hand 🙋‍♀️`
- `Follow me for more [topic] content`

### 3. Hashtag Rules

Each note should include **8 hashtags**, distributed as follows:

| Category | Count | Description | Example |
|------|------|------|------|
| Core keywords | 2 | Precise keywords for the note's topic | `#MaskRecommendations` `#HydratingMask` |
| Category words | 2 | Broader category/domain | `#Skincare` `#BeautyProducts` |
| Scenario words | 2 | Usage scenario/target audience | `#StudentSkincare` `#SeasonalSkincare` |
| Trending words | 2 | Platform trending topics | `#GoodThingsSharing` `#MyFavorites` |

**Selection principles:**
- Prioritize tags with high search volume but moderate competition
- Avoid overly broad tags (e.g., `#Life`)
- Include long-tail keywords to boost search visibility
- Follow the platform's current trending topic list

### 4. Image Style Recommendations

Xiaohongshu is a visually driven platform — the cover image determines 80% of the click-through rate.

**10 recommended visual styles:**

| # | Style | Best for | Key points |
|------|------|---------|------|
| 1 | Split-screen comparison | Reviews/results showcase | Side-by-side or top-bottom comparison, label differences |
| 2 | List image | Product roundups/recommendations | White background 9-grid product display |
| 3 | Tutorial step image | Tutorials | Numbered steps, clear and easy to follow |
| 4 | Text cover | Knowledge sharing | Large title + clean background color |
| 5 | Lifestyle atmosphere shot | Lifestyle sharing/outfits | Natural light, authentic feel |
| 6 | Data chart | Reviews/science | Simplified data visualization |
| 7 | Hand-drawn/illustration style | Knowledge popularization | Cute-style infographics |
| 8 | Vlog screenshot | Daily sharing | Key video frame + text annotation |
| 9 | Close-up product shot | Product recommendations | HD detail, highlight texture |
| 10 | Instagram-style minimal | Fashion/home | Low saturation, premium feel |

**Cover design general principles:**
- Aspect ratio: **3:4** (1080×1440px) is best
- Text should not exceed **20%** of the cover area
- Place core information in the upper half of the image (feed stream crop safe zone)
- Vibrant colors, high contrast
- Avoid excessive retouching, keep it authentic

## Content Type Workflows

### Workflow 1: Product Recommendation Note

```
Input → Product name, category, price, target audience
  ↓
Step 1: Generate 3 title options (pain point / number / scene method)
  ↓
Step 2: Write body text
  - Hook: Personal usage experience / how I discovered it
  - Core: Product highlights (3-5), usage method, suitable audience
  - Summary: One-sentence recommendation reason
  - CTA: Encourage saving and discussion
  ↓
Step 3: Generate 8 hashtags
  ↓
Step 4: Cover suggestion (recommended style 9 close-up or style 2 list image)
  ↓
Output → Complete note (ready to publish)
```

### Workflow 2: Review Note

```
Input → Product list (2-5), review dimensions
  ↓
Step 1: Title uses comparison or number method
  ↓
Step 2: Write body text
  - Hook: Review motivation/pain point introduction
  - Core: Item-by-item comparison (ingredients/price/usage/value)
  - Summary: Scoring or ranking for each product
  - CTA: `Which one have you used? Chat in the comments`
  ↓
Step 3: Hashtags (add brand-related tags)
  ↓
Step 4: Cover suggestion (recommended style 1 split comparison or style 6 data chart)
  ↓
Output → Complete review note
```

### Workflow 3: Tutorial Note

```
Input → Tutorial topic, difficulty level, target audience
  ↓
Step 1: Title uses number method (`Master X in ... steps`)
  ↓
Step 2: Write body text
  - Hook: The value/result after learning
  - Core: Step-by-step guide (1-2 sentences per step)
  - Summary: Key notes/precautions
  - CTA: `Drop a ✅ if you got it`
  ↓
Step 3: Hashtags (add `#Tutorial` `#StepByStep` etc.)
  ↓
Step 4: Cover suggestion (recommended style 3 step image or style 4 text cover)
  ↓
Output → Complete tutorial note + multi-image suggestions (one image per step)
```

### Workflow 4: Lifestyle Sharing Note

```
Input → Sharing topic, scenario, emotional tone
  ↓
Step 1: Title uses resonance or scene method
  ↓
Step 2: Write body text
  - Hook: Story opening / emotionalentry
  - Core: Sharing details, personal feelings, practical info
  - Summary: Takeaway or reflection
  - CTA: `Have similar experiences? Share in the comments`
  ↓
Step 3: Hashtags (scene-based + emotional tags)
  ↓
Step 4: Cover suggestion (recommended style 5 lifestyle atmosphere or style 8 Vlog screenshot)
  ↓
Output → Complete lifestyle sharing note
```

## Quality Checklist

Before publishing, verify:

- [ ] Title within 20 characters, 1-2 emoji, no `!!!`
- [ ] Body has Hook → Core → Summary → CTA structure
- [ ] 8 hashtags with proper category distribution (2+2+2+2)
- [ ] Image aspect ratio 3:4, text under 20% of cover
- [ ] Content feels authentic and personal
- [ ] No exaggerated or misleading claims
- [ ] Hashtags include core, category, scenario, and trending words

## Output Format

```markdown
# [Title with emoji]

[Body text following Hook → Core → Summary → CTA structure]

---
#hashtag1 #hashtag2 #hashtag3 #hashtag4 #hashtag5 #hashtag6 #hashtag7 #hashtag8

**Cover suggestion**: Style [#] — [brief description]
```
