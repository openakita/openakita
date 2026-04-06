---
name: ai-music-generation
description: "Generate AI music and songs with Diffrythm, Tencent Song Generation via inference.sh CLI. Models: Diffrythm (fast song generation), Tencent Song Generation (full songs with vocals). Capabilities: text-to-music, song generation, instrumental, lyrics to song, soundtrack creation. Use for: background music, social media content, game soundtracks, podcasts, royalty-free music. Triggers: music generation, ai music, generate song, ai composer, text to music, song generator, create music with ai, suno alternative, udio alternative, ai song, ai soundtrack, generate soundtrack, ai jingle, music ai, beat generator"
allowed-tools: Bash(infsh *)
---

# AI Music Generation

Generate music and songs via [inference.sh](https://inference.sh) CLI.

![AI Music Generation](https://cloud.inference.sh/u/4mg21r6ta37mpaz6ktzwtt8krr/01jz01qvx0gdcyvhvhpfjjb6s4.png)

## Quick Start

```bash
# Install CLI
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a song
infsh app run infsh/diffrythm --input '{"prompt": "upbeat electronic dance track"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Models

| Model | App ID | Best For |
|-------|--------|----------|
| Diffrythm | `infsh/diffrythm` | Fast song generation |
| Tencent Song | `infsh/tencent-song-generation` | Full songs with vocals |

## Browse Audio Apps

```bash
infsh app list --category audio
```

## Examples

### Instrumental Track

```bash
infsh app run infsh/diffrythm --input '{
  "prompt": "cinematic orchestral soundtrack, epic and dramatic"
}'
```

### Song with Vocals

```bash
infsh app sample infsh/tencent-song-generation --save input.json

# Edit input.json:
# {
#   "prompt": "pop song about summer love",
#   "lyrics": "Walking on the beach with you..."
# }

infsh app run infsh/tencent-song-generation --input input.json
```

### Background Music for Video

```bash
infsh app run infsh/diffrythm --input '{
  "prompt": "calm lo-fi hip hop beat, study music, relaxing"
}'
```

### Podcast Intro

```bash
infsh app run infsh/diffrythm --input '{
  "prompt": "short podcast intro jingle, professional, tech themed, 10 seconds"
}'
```

### Game Soundtrack

```bash
infsh app run infsh/diffrythm --input '{
  "prompt": "retro 8-bit video game music, adventure theme, chiptune"
}'
```

## Prompt Tips

**Genre keywords**: pop, rock, electronic, jazz, classical, hip-hop, lo-fi, ambient, orchestral

**Mood keywords**: happy, sad, energetic, calm, dramatic, epic, mysterious, uplifting

**Instrument keywords**: piano, guitar, synth, drums, strings, brass, choir

**Structure keywords**: intro, verse, chorus, bridge, outro, loop

## Use Cases

- **Social Media**: Background music for videos
- **Podcasts**: Intro/outro jingles
- **Games**: Soundtracks and effects
- **Videos**: Background scores
- **Ads**: Commercial jingles
- **Content Creation**: Royalty-free music

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# Text-to-speech
npx skills add inference-sh/skills@text-to-speech

# Video generation (add music to videos)
npx skills add inference-sh/skills@ai-video-generation

# Speech-to-text
npx skills add inference-sh/skills@speech-to-text
```

Browse all apps: `infsh app list`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Content Pipeline Example](https://inference.sh/docs/examples/content-pipeline) - Building media workflows
- [Apps Overview](https://inference.sh/docs/apps/overview) - Understanding the app ecosystem
