---
name: email-design
description: "Email marketing design with layout patterns, subject line formulas, and deliverability rules. Covers welcome sequences, promotional emails, transactional templates, and mobile optimization. Use for: email marketing, newsletter design, drip campaigns, email templates, transactional emails. Triggers: email design, email template, email marketing, newsletter design, email layout, email campaign, drip campaign, welcome email, promotional email, transactional email, email subject line, email header image, email banner"
allowed-tools: Bash(infsh *)
---

# Email Design

Design high-converting marketing emails with AI-generated visuals via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate email header banner
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:600px;height:250px;background:linear-gradient(135deg,#667eea,#764ba2);display:flex;align-items:center;justify-content:center;font-family:system-ui;color:white;text-align:center\"><div><h1 style=\"font-size:36px;margin:0\">Spring Sale — 30% Off</h1><p style=\"font-size:18px;opacity:0.9\">This weekend only</p></div></div>"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Email Width & Layout

| Constraint | Value | Why |
|-----------|-------|-----|
| **Max width** | 600px | Gmail, Outlook rendering standard |
| **Mobile width** | 320-414px | Responsive fallback |
| **Single column** | Preferred | Better mobile rendering |
| **Two column** | Use sparingly | Breaks on many clients |
| **Image width** | 600px max, 300px for 2-col | Retina: provide 2x (1200px) |
| **Font size (body)** | 14-16px | Below 14px is hard to read on mobile |
| **Font size (heading)** | 22-28px | Must be scannable |
| **Line height** | 1.5 | Readability on all devices |

### The Inverted Pyramid Layout

The most effective email layout funnels attention to a single CTA:

```
┌──────────────────────────────────┐
│           HEADER IMAGE           │  ← Brand/visual hook
│          (600 x 200-300)         │
├──────────────────────────────────┤
│                                  │
│     Headline (one line)          │  ← What's this about
│                                  │
│     2-3 sentences of body copy   │  ← Why should I care
│     explaining the value.        │
│                                  │
│        ┌──────────────┐          │
│        │   CTA BUTTON  │         │  ← One clear action
│        └──────────────┘          │
│                                  │
├──────────────────────────────────┤
│     Footer: Unsubscribe link     │
└──────────────────────────────────┘
```

## Subject Lines

### Formulas That Work

| Formula | Example | Open Rate Impact |
|---------|---------|-----------------|
| Number + benefit | "5 ways to cut your build time in half" | High |
| Question | "Are you still deploying on Fridays?" | High |
| How-to | "How to automate your reports in 3 steps" | Medium-High |
| Urgency (genuine) | "Last day: 30% off annual plans" | High (if real) |
| Personalized | "[Name], your weekly report is ready" | Very High |
| Curiosity gap | "The one feature our users can't stop talking about" | Medium-High |

### Rules

| Rule | Value |
|------|-------|
| **Length** | 30-50 characters (mobile truncates at ~35) |
| **Preview text** | First 40-100 chars after subject — design this intentionally |
| **Emoji** | Max 1, at start or end, test with your audience |
| **ALL CAPS** | Never — triggers spam filters |
| **Spam trigger words** | Avoid: "free", "act now", "limited time", "click here" in subject |
| **Personalization** | [First name] in subject lifts open rates 20%+ |

### Preview Text

The preview text appears after the subject line in the inbox. Don't waste it.

```
❌ "View this email in your browser" (default, wasted space)
❌ "Having trouble viewing this?" (no one cares)

✅ Subject: "5 ways to cut build time"
   Preview: "Number 3 saved us 6 hours per week"

✅ Subject: "Your monthly report is ready"
   Preview: "Revenue up 23% — here's what drove it"
```

## Email Types

### Welcome Email (Automated, Day 0)

| Element | Content |
|---------|---------|
| Subject | "Welcome to [Product] — here's what's next" |
| Header | Brand image or product screenshot |
| Body | 3-4 sentences: what they signed up for, what to expect, one quick win |
| CTA | "Complete your setup" or "Try your first [action]" |
| Timing | Immediately after signup |

### Promotional / Campaign

| Element | Content |
|---------|---------|
| Subject | Benefit-focused, urgency if real |
| Header | Hero image showing the offer/outcome |
| Body | Problem → solution → offer → deadline |
| CTA | "Get 30% Off" or "Start Free Trial" |
| Urgency | Real deadline, not fake scarcity |

### Product Update / Changelog

| Element | Content |
|---------|---------|
| Subject | "New: [Feature name] is here" |
| Header | Screenshot or visual of the feature |
| Body | What's new, why it matters, how to use it |
| CTA | "Try [feature]" |

### Transactional (Receipts, Confirmations)

| Rule | Why |
|------|-----|
| Clear purpose in subject | "Your order #1234 is confirmed" |
| Minimal design | Don't confuse with marketing |
| Key info above the fold | Order number, amount, date |
| No promotional content (mostly) | CAN-SPAM allows some, but keep minimal |

## Header Image Design

```bash
# Welcome email header
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:600px;height:250px;background:linear-gradient(135deg,#2d3436,#636e72);display:flex;align-items:center;padding:40px;font-family:system-ui;color:white\"><div><p style=\"font-size:14px;text-transform:uppercase;letter-spacing:2px;opacity:0.7;margin:0\">Welcome to</p><h1 style=\"font-size:42px;margin:8px 0 0;font-weight:800\">DataFlow</h1><p style=\"font-size:18px;opacity:0.8;margin-top:4px\">Your data, automated</p></div></div>"
}'

# Sale / promotional header
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:600px;height:300px;background:linear-gradient(135deg,#e74c3c,#c0392b);display:flex;align-items:center;justify-content:center;font-family:system-ui;color:white;text-align:center\"><div><p style=\"font-size:20px;opacity:0.9;margin:0\">This Weekend Only</p><h1 style=\"font-size:72px;margin:8px 0;font-weight:900\">30% OFF</h1><p style=\"font-size:18px;opacity:0.8\">All annual plans. Ends Sunday.</p></div></div>"
}'

# Feature announcement header with AI visual
infsh app run falai/flux-dev-lora --input '{
  "prompt": "clean modern email header banner, abstract flowing data visualization, dark blue gradient background, subtle glowing nodes and connections, tech aesthetic, minimal, no text, 600x250 equivalent",
  "width": 1200,
  "height": 500
}'
```

## CTA Buttons

| Rule | Value |
|------|-------|
| **Width** | 200-300px, not full width |
| **Height** | 44-50px minimum (tap target) |
| **Color** | High contrast with background |
| **Text** | Action verb + outcome: "Start Free Trial" |
| **Shape** | Rounded corners (4-8px border-radius) |
| **Placement** | Above the fold, repeated at bottom for long emails |
| **Quantity** | ONE primary CTA per email |

### Bulletproof Buttons

HTML buttons render differently across email clients. Use the "bulletproof button" technique (VML for Outlook, HTML/CSS for everything else):

```html
<!-- Bulletproof button (works everywhere including Outlook) -->
<table cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td align="center" bgcolor="#22c55e" style="border-radius:6px;">
      <a href="https://yoursite.com/action" target="_blank"
         style="font-size:16px;font-family:sans-serif;color:#ffffff;
                text-decoration:none;padding:12px 24px;display:inline-block;
                font-weight:bold;">
        Start Free Trial
      </a>
    </td>
  </tr>
</table>
```

## Mobile Optimization

| Rule | Why |
|------|-----|
| Single column layout | Multi-column breaks on mobile |
| Font minimum 14px | Smaller is unreadable |
| CTA button minimum 44px tall | Apple/Android tap target |
| Images scale to 100% width | Prevent horizontal scroll |
| Stack elements vertically | Side-by-side breaks on narrow screens |
| Test on Gmail app, Apple Mail, Outlook | The big 3 email clients |

**60%+ of emails are opened on mobile.** Design mobile-first.

## Deliverability Checklist

| Factor | Rule |
|--------|------|
| Image-to-text ratio | Max 40% images, 60% text (spam filters flag image-heavy emails) |
| Alt text on images | Always — images blocked by default in many clients |
| Unsubscribe link | Required by law (CAN-SPAM, GDPR) — make it easy to find |
| From name | Recognizable person or brand name |
| Reply-to | Real address, not no-reply@ (hurts deliverability) |
| List hygiene | Remove bounces, clean inactive subscribers quarterly |
| SPF/DKIM/DMARC | Technical authentication — set up once, critical for inbox |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| No preview text | Shows "View in browser" or random code | Set preview text intentionally |
| Image-only emails | Blocked images = blank email + spam risk | 60%+ text, alt text on images |
| Multiple CTAs | Decision paralysis, lower click rate | One primary CTA per email |
| Tiny text | Unreadable on mobile | Minimum 14px body, 22px headings |
| no-reply@ sender | Hurts deliverability, feels impersonal | Use real reply address |
| No mobile testing | Broken layout for 60%+ of readers | Test on Gmail app + Apple Mail |
| Missing unsubscribe | Illegal (CAN-SPAM) + spam complaints | Clear unsubscribe link in footer |
| Over-designing | Email clients render CSS inconsistently | Simple layouts, inline styles |
| Fake urgency | Erodes trust, trains users to ignore | Only use real deadlines |

## Related Skills

```bash
npx skills add inference-sh/skills@landing-page-design
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
