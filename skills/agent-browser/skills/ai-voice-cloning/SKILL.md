---
name: ai-voice-cloning
description: "AI voice generation, text-to-speech, and voice synthesis via inference.sh CLI. Models: Kokoro TTS, DIA, Chatterbox, Higgs, VibeVoice for natural speech. Capabilities: multiple voices, emotions, accents, long-form narration, conversation. Use for: voiceovers, audiobooks, podcasts, video narration, accessibility. Triggers: voice cloning, tts, text to speech, ai voice, voice generation, voice synthesis, voice over, narration, speech synthesis, ai narrator, elevenlabs alternative, natural voice, realistic speech, voice ai"
allowed-tools: Bash(infsh *)
---

# AI Voice Generation

Generate natural AI voices via [inference.sh](https://inference.sh) CLI.

![AI Voice Generation](https://cloud.inference.sh/u/4mg21r6ta37mpaz6ktzwtt8krr/01jz00krptarq4bwm89g539aea.png)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate speech
infsh app run infsh/kokoro-tts --input '{
  "text": "Hello! This is an AI-generated voice that sounds natural and engaging.",
  "voice": "af_sarah"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Models

| Model | App ID | Best For |
|-------|--------|----------|
| Kokoro TTS | `infsh/kokoro-tts` | Natural, multiple voices |
| DIA | `infsh/dia-tts` | Conversational, expressive |
| Chatterbox | `infsh/chatterbox` | Casual, entertainment |
| Higgs | `infsh/higgs-tts` | Professional narration |
| VibeVoice | `infsh/vibevoice` | Emotional range |

## Kokoro Voice Library

### American English

| Voice ID | Gender | Style |
|----------|--------|-------|
| `af_sarah` | Female | Warm, friendly |
| `af_nicole` | Female | Professional |
| `af_sky` | Female | Youthful |
| `am_michael` | Male | Authoritative |
| `am_adam` | Male | Conversational |
| `am_echo` | Male | Clear, neutral |

### British English

| Voice ID | Gender | Style |
|----------|--------|-------|
| `bf_emma` | Female | Refined |
| `bf_isabella` | Female | Warm |
| `bm_george` | Male | Classic |
| `bm_lewis` | Male | Modern |

## Voice Generation Examples

### Professional Narration

```bash
infsh app run infsh/kokoro-tts --input '{
  "text": "Welcome to our quarterly earnings call. Today we will discuss the financial performance and strategic initiatives for the past quarter.",
  "voice": "am_michael",
  "speed": 1.0
}'
```

### Conversational Style

```bash
infsh app run infsh/dia-tts --input '{
  "text": "Hey, so I was thinking about that project we discussed. What if we tried a different approach?",
  "voice": "conversational"
}'
```

### Audiobook Narration

```bash
infsh app run infsh/kokoro-tts --input '{
  "text": "Chapter One. The morning mist hung low over the valley as Sarah made her way down the winding path. She had been walking for hours.",
  "voice": "bf_emma",
  "speed": 0.9
}'
```

### Video Voiceover

```bash
infsh app run infsh/kokoro-tts --input '{
  "text": "Introducing the next generation of productivity. Work smarter, not harder.",
  "voice": "af_nicole",
  "speed": 1.1
}'
```

### Podcast Host

```bash
infsh app run infsh/kokoro-tts --input '{
  "text": "Welcome back to Tech Talk! Im your host, and today we are diving deep into the world of artificial intelligence.",
  "voice": "am_adam"
}'
```

## Multi-Voice Conversation

```bash
# Generate dialogue between two speakers
# Speaker 1
infsh app run infsh/kokoro-tts --input '{
  "text": "Have you seen the latest AI developments? Its incredible how fast things are moving.",
  "voice": "am_michael"
}' > speaker1.json

# Speaker 2
infsh app run infsh/kokoro-tts --input '{
  "text": "I know, right? Just last week I tried that new image generator and was blown away.",
  "voice": "af_sarah"
}' > speaker2.json

# Merge conversation
infsh app run infsh/media-merger --input '{
  "audio_files": ["<speaker1-url>", "<speaker2-url>"],
  "crossfade_ms": 300
}'
```

## Long-Form Content

### Chunked Processing

For content over 5000 characters, split into chunks:

```bash
# Process long text in chunks
TEXT="Your very long text here..."

# Split and generate
# Chunk 1
infsh app run infsh/kokoro-tts --input '{
  "text": "<chunk-1>",
  "voice": "bf_emma"
}' > chunk1.json

# Chunk 2
infsh app run infsh/kokoro-tts --input '{
  "text": "<chunk-2>",
  "voice": "bf_emma"
}' > chunk2.json

# Merge chunks
infsh app run infsh/media-merger --input '{
  "audio_files": ["<chunk1-url>", "<chunk2-url>"],
  "crossfade_ms": 100
}'
```

## Voice + Video Workflow

### Add Voiceover to Video

```bash
# 1. Generate voiceover
infsh app run infsh/kokoro-tts --input '{
  "text": "This stunning footage shows the beauty of nature in its purest form.",
  "voice": "am_michael"
}' > voiceover.json

# 2. Merge with video
infsh app run infsh/media-merger --input '{
  "video_url": "https://your-video.mp4",
  "audio_url": "<voiceover-url>"
}'
```

### Create Talking Head

```bash
# 1. Generate speech
infsh app run infsh/kokoro-tts --input '{
  "text": "Hi, Im excited to share some updates with you today.",
  "voice": "af_sarah"
}' > speech.json

# 2. Animate with avatar
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "https://portrait.jpg",
  "audio_url": "<speech-url>"
}'
```

## Speed and Pacing

| Speed | Effect | Use For |
|-------|--------|---------|
| 0.8 | Slow, deliberate | Audiobooks, meditation |
| 0.9 | Slightly slow | Education, tutorials |
| 1.0 | Normal | General purpose |
| 1.1 | Slightly fast | Commercials, energy |
| 1.2 | Fast | Quick announcements |

```bash
# Slow narration
infsh app run infsh/kokoro-tts --input '{
  "text": "Take a deep breath. Let yourself relax.",
  "voice": "bf_emma",
  "speed": 0.8
}'
```

## Punctuation for Pacing

Use punctuation to control speech rhythm:

| Punctuation | Effect |
|-------------|--------|
| Period `.` | Full pause |
| Comma `,` | Brief pause |
| `...` | Extended pause |
| `!` | Emphasis |
| `?` | Question intonation |
| `-` | Quick break |

```bash
infsh app run infsh/kokoro-tts --input '{
  "text": "Wait... Did you hear that? Something is coming. Something big!",
  "voice": "am_adam"
}'
```

## Best Practices

1. **Match voice to content** - Professional voice for business, casual for social
2. **Use punctuation** - Control pacing with periods and commas
3. **Keep sentences short** - Easier to generate and sounds more natural
4. **Test different voices** - Same text sounds different across voices
5. **Adjust speed** - Slightly slower often sounds more natural
6. **Break long content** - Process in chunks for consistency

## Use Cases

- **Voiceovers** - Video narration, commercials
- **Audiobooks** - Full book narration
- **Podcasts** - AI hosts and guests
- **E-learning** - Course narration
- **Accessibility** - Screen reader content
- **IVR** - Phone system messages
- **Content localization** - Translate and voice

## Related Skills

```bash
# All TTS models
npx skills add inference-sh/skills@text-to-speech

# Podcast creation
npx skills add inference-sh/skills@ai-podcast-creation

# AI avatars
npx skills add inference-sh/skills@ai-avatar-video

# Video generation
npx skills add inference-sh/skills@ai-video-generation

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

Browse audio apps: `infsh app list --category audio`
