---
name: product-changelog
description: "Product changelog and release notes that users actually read. Covers categorization, user-facing language, visuals, and distribution. Use for: release notes, changelogs, product updates, feature announcements, versioning. Triggers: changelog, release notes, product update, version notes, what's new, feature announcement, product changelog, update log, release announcement, version release, product release, ship notes"
allowed-tools: Bash(infsh *)
---

# Product Changelog

Write changelogs and release notes that users read and care about via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a feature announcement visual
infsh app run falai/flux-dev-lora --input '{
  "prompt": "clean product UI screenshot mockup, modern dashboard interface showing a new analytics chart feature, light mode, minimal design, professional SaaS product",
  "width": 1248,
  "height": 832
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Entry Format

### The Anatomy of a Good Entry

```markdown
### New: Bulk Export for Reports 📊

You can now export up to 10,000 rows at once from any report view.
Select your rows, click Export, and choose CSV or Excel format.

Previously limited to 500 rows per export.

![Bulk export button in the reports toolbar](screenshot.png)
```

**Structure:** Category label -> User-facing title -> What you can do now -> How -> What changed -> Visual

### User-Facing Language

```
❌ Internal language:
"Implemented batch processing queue for the export service"
"Refactored the ReportExporter class to support pagination"
"Fixed bug in CSV serialization (PR #4521)"

✅ User-facing language:
"You can now export up to 10,000 rows at once from any report"
"Reports now load 3x faster when filtering large datasets"
"Fixed an issue where exported CSV files had missing columns"
```

**Rules:**
- Write what the user can DO, not what you BUILT
- Start with "You can now..." / "Reports now..." / "Fixed an issue where..."
- Include the benefit, not just the mechanism
- Use present tense

## Categories

### Standard Categories

| Category | Color | Icon | Use For |
|----------|-------|------|---------|
| **New** | Green | ✨ or 🆕 | Entirely new features or capabilities |
| **Improved** | Blue | ⚡ or 🔧 | Enhancements to existing features |
| **Fixed** | Yellow/Orange | 🐛 or 🔨 | Bug fixes |
| **Removed** | Red | 🗑️ or ⚠️ | Deprecated or removed features |
| **Security** | Purple | 🔒 | Security patches |

### Categorization Rules

- **New** = something users couldn't do before at all
- **Improved** = something users could do, now it's better/faster/easier
- **Fixed** = something that was broken, now works correctly
- Don't use "Updated" — it's meaningless. Was it improved or fixed?

## Version Numbering

### Semantic Versioning (SemVer)

```
MAJOR.MINOR.PATCH
  3   .  2  .  1
```

| Component | Increment When | Example |
|-----------|---------------|---------|
| MAJOR | Breaking changes, major redesign | 2.0.0 -> 3.0.0 |
| MINOR | New features, backward-compatible | 3.1.0 -> 3.2.0 |
| PATCH | Bug fixes, small improvements | 3.2.0 -> 3.2.1 |

### Date-Based Versioning

```
2026-02-08  or  2026.02.08  or  February 8, 2026
```

Best for SaaS products with continuous deployment.

## Changelog Page Structure

```markdown
# Changelog

## February 8, 2026

### New
- **Bulk Export for Reports** — Export up to 10,000 rows at once. [Learn more →](link)
- **Dark Mode** — Toggle dark mode from Settings > Appearance.

### Improved
- **Dashboard Loading** — Dashboards now load 3x faster on large datasets.
- **Search** — Search results now include archived items.

### Fixed
- Fixed an issue where exported CSV files had missing column headers.
- Fixed a bug where the date picker showed incorrect timezone.

---

## February 1, 2026

### New
- **API Webhooks** — Get notified when events happen in your account.

### Fixed
- Fixed an issue where email notifications were delayed by up to 2 hours.
```

## Visual Changelogs

### When to Add Visuals

| Change Type | Visual |
|-------------|--------|
| New UI feature | Screenshot of the new feature |
| UI redesign | Before/after comparison |
| New workflow | Step-by-step screenshots or short video |
| Performance improvement | Chart showing improvement |
| Complex feature | Animated GIF or video demo |

### Generating Visuals

```bash
# Feature screenshot (if you have the app running, use agent browser)
infsh app run infsh/agent-browser --input '{
  "url": "https://your-app.com/new-feature",
  "action": "screenshot"
}'

# Before/after comparison
infsh app run infsh/stitch-images --input '{
  "images": ["before-screenshot.png", "after-screenshot.png"],
  "direction": "horizontal"
}'

# Annotated screenshot with callout
infsh app run bytedance/seededit-3-0-i2i --input '{
  "prompt": "add a red circle highlight around the export button in the top right area",
  "image": "screenshot.png"
}'

# Feature announcement banner
infsh app run falai/flux-dev-lora --input '{
  "prompt": "clean modern product announcement banner, gradient blue to purple background, abstract geometric shapes, professional SaaS aesthetic, wide format",
  "width": 1248,
  "height": 832
}'
```

## Breaking Changes

Breaking changes need special treatment:

```markdown
### ⚠️ Breaking: API v2 Endpoints Deprecated

**What changed:** API v1 endpoints will stop working on March 15, 2026.

**What you need to do:**
1. Update your API calls to use v2 endpoints ([migration guide →](link))
2. Update authentication to use Bearer tokens instead of API keys
3. Test your integration before March 15

**Timeline:**
- Now: v2 endpoints available, v1 still works
- March 1: v1 returns deprecation warnings
- March 15: v1 stops working

If you need help migrating, contact support@company.com.
```

## Distribution Channels

| Channel | Format | When |
|---------|--------|------|
| **Changelog page** | Full detail, all entries | Every release |
| **In-app notification** | 1-2 line summary | New features, breaking changes |
| **Email** | Curated highlights, visuals | Major releases (monthly/quarterly) |
| **Blog post** | Deep dive with context | Big launches |
| **Social media** | Single feature highlight | Notable features |
| **Slack/Discord** | Brief announcement | If you have a community |

### Social Media Snippet Format

```
🆕 New in [Product]: [Feature Name]

[1-2 sentence description of what you can now do]

[Screenshot or demo video]

Try it now → [link]
```

## Writing Tips

### Do

- Group related changes together
- Lead with the biggest/most requested change
- Link to documentation for complex features
- Include who requested it ("By popular request:")
- Show migration paths for breaking changes
- Date every entry

### Don't

- Don't say "various bug fixes" — list specific fixes or skip them
- Don't include internal references (PR numbers, ticket IDs, branch names)
- Don't use "Updated [feature]" without saying how
- Don't list changes nobody cares about (dependency bumps, internal refactors)
- Don't commit-dump — one changelog entry may represent many commits

## Changelog Frequency

| Product Type | Frequency | Notes |
|-------------|-----------|-------|
| SaaS (continuous deploy) | Weekly batch | Group a week of changes |
| SaaS (major features) | Per feature launch | With blog post |
| Versioned software | Per version release | Tied to semver |
| API | Per version + deprecation notices | Include migration guides |
| Mobile app | Per app store release | Match store listing "What's New" |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Developer language | Users don't understand | Write what users can do |
| "Bug fixes and improvements" | Zero information | List specific fixes |
| No dates | Can't tell what's new | Date every entry |
| No visuals | Users skip text | Screenshot major features |
| Breaking changes buried | Users discover too late | Prominent warning + timeline |
| Commit log as changelog | Noisy, unhelpful | Curate and rewrite |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
