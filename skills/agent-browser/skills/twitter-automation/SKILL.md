---
name: twitter-automation
description: "Automate Twitter/X with posting, engagement, and user management via inference.sh CLI. Apps: x/post-tweet, x/post-create (with media), x/post-like, x/post-retweet, x/dm-send, x/user-follow. Capabilities: post tweets, schedule content, like posts, retweet, send DMs, follow users, get profiles. Use for: social media automation, content scheduling, engagement bots, audience growth, X API. Triggers: twitter api, x api, tweet automation, post to twitter, twitter bot, social media automation, x automation, tweet scheduler, twitter integration, post tweet, twitter post, x post, send tweet"
allowed-tools: Bash(infsh *)
---

# Twitter/X Automation

Automate Twitter/X via [inference.sh](https://inference.sh) CLI.

![Twitter/X Automation](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kgad3pxsh3z3hnfpjyjpx4x4.jpeg)

## Quick Start

```bash
# Install CLI
curl -fsSL https://cli.inference.sh | sh && infsh login

# Post a tweet
infsh app run x/post-tweet --input '{"text": "Hello from inference.sh!"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Apps

| App | App ID | Description |
|-----|--------|-------------|
| Post Tweet | `x/post-tweet` | Post text tweets |
| Create Post | `x/post-create` | Post with media |
| Like Post | `x/post-like` | Like a tweet |
| Retweet | `x/post-retweet` | Retweet a post |
| Delete Post | `x/post-delete` | Delete a tweet |
| Get Post | `x/post-get` | Get tweet by ID |
| Send DM | `x/dm-send` | Send direct message |
| Follow User | `x/user-follow` | Follow a user |
| Get User | `x/user-get` | Get user profile |

## Examples

### Post a Tweet

```bash
infsh app run x/post-tweet --input '{"text": "Just shipped a new feature! 🚀"}'
```

### Post with Media

```bash
infsh app sample x/post-create --save input.json

# Edit input.json:
# {
#   "text": "Check out this AI-generated image!",
#   "media_url": "https://your-image-url.jpg"
# }

infsh app run x/post-create --input input.json
```

### Like a Tweet

```bash
infsh app run x/post-like --input '{"tweet_id": "1234567890"}'
```

### Retweet

```bash
infsh app run x/post-retweet --input '{"tweet_id": "1234567890"}'
```

### Send a DM

```bash
infsh app run x/dm-send --input '{
  "recipient_id": "user_id_here",
  "text": "Hey! Thanks for the follow."
}'
```

### Follow a User

```bash
infsh app run x/user-follow --input '{"username": "elonmusk"}'
```

### Get User Profile

```bash
infsh app run x/user-get --input '{"username": "OpenAI"}'
```

### Get Tweet Details

```bash
infsh app run x/post-get --input '{"tweet_id": "1234567890"}'
```

### Delete a Tweet

```bash
infsh app run x/post-delete --input '{"tweet_id": "1234567890"}'
```

## Workflow: Generate AI Image and Post

```bash
# 1. Generate image
infsh app run falai/flux-dev-lora --input '{"prompt": "sunset over mountains"}' > image.json

# 2. Post to Twitter with the image URL
infsh app run x/post-create --input '{
  "text": "AI-generated art of a sunset 🌅",
  "media_url": "<image-url-from-step-1>"
}'
```

## Workflow: Generate and Post Video

```bash
# 1. Generate video
infsh app run google/veo-3-1-fast --input '{"prompt": "waves on a beach"}' > video.json

# 2. Post to Twitter
infsh app run x/post-create --input '{
  "text": "AI-generated video 🎬",
  "media_url": "<video-url-from-step-1>"
}'
```

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# Image generation (create images to post)
npx skills add inference-sh/skills@ai-image-generation

# Video generation (create videos to post)
npx skills add inference-sh/skills@ai-video-generation

# AI avatars (create presenter videos)
npx skills add inference-sh/skills@ai-avatar-video
```

Browse all apps: `infsh app list`

## Documentation

- [X.com Integration](https://inference.sh/docs/integrations/x) - Setting up Twitter/X integration
- [X.com Integration Example](https://inference.sh/docs/examples/x-integration) - Complete Twitter workflow
- [Apps Overview](https://inference.sh/docs/apps/overview) - Understanding the app ecosystem
