---
name: remotion-render
description: "Render videos from React/Remotion component code via inference.sh. Pass TSX code, get MP4. Supports all Remotion APIs: useCurrentFrame, useVideoConfig, spring, interpolate, AbsoluteFill, Sequence. Configurable resolution, FPS, duration, codec. Use for: programmatic video generation, animated graphics, motion design, data-driven videos, React animations to video. Triggers: remotion, render video from code, tsx to video, react video, programmatic video, remotion render, code to video, animated video, motion graphics code, react animation video"
allowed-tools: Bash(infsh *)
---

# Remotion Render

Render videos from React/Remotion component code via [inference.sh](https://inference.sh) CLI.

![Remotion Render](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg2c0egyg243mnyth4y6g51q.jpeg)

## Quick Start

```bash
# Install CLI
curl -fsSL https://cli.inference.sh | sh && infsh login

# Render a simple animation
infsh app run infsh/remotion-render --input '{
  "code": "import { useCurrentFrame, AbsoluteFill } from \"remotion\"; export default function Main() { const frame = useCurrentFrame(); return <AbsoluteFill style={{backgroundColor: \"#000\", display: \"flex\", justifyContent: \"center\", alignItems: \"center\"}}><h1 style={{color: \"white\", fontSize: 100, opacity: frame / 30}}>Hello World</h1></AbsoluteFill>; }",
  "duration_seconds": 3,
  "fps": 30,
  "width": 1920,
  "height": 1080
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Input Schema

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | Yes | React component TSX code. Must export default a component. |
| `composition_id` | string | No | Composition ID to render |
| `props` | object | No | Props passed to the component |
| `width` | number | No | Video width in pixels |
| `height` | number | No | Video height in pixels |
| `fps` | number | No | Frames per second |
| `duration_seconds` | number | No | Video duration in seconds |
| `codec` | string | No | Output codec |

## Available Imports

Your TSX code can import from `remotion` and `react`:

```tsx
// Remotion APIs
import {
  useCurrentFrame,
  useVideoConfig,
  spring,
  interpolate,
  AbsoluteFill,
  Sequence,
  Audio,
  Video,
  Img
} from "remotion";

// React
import React, { useState, useEffect } from "react";
```

## Examples

### Fade-In Text

```bash
infsh app run infsh/remotion-render --input '{
  "code": "import { useCurrentFrame, AbsoluteFill, interpolate } from \"remotion\"; export default function Main() { const frame = useCurrentFrame(); const opacity = interpolate(frame, [0, 30], [0, 1]); return <AbsoluteFill style={{backgroundColor: \"#1a1a2e\", display: \"flex\", justifyContent: \"center\", alignItems: \"center\"}}><h1 style={{color: \"#eee\", fontSize: 80, opacity}}>Welcome</h1></AbsoluteFill>; }",
  "duration_seconds": 2,
  "fps": 30,
  "width": 1920,
  "height": 1080
}'
```

### Animated Counter

```bash
infsh app run infsh/remotion-render --input '{
  "code": "import { useCurrentFrame, useVideoConfig, AbsoluteFill } from \"remotion\"; export default function Main() { const frame = useCurrentFrame(); const { fps, durationInFrames } = useVideoConfig(); const progress = Math.floor((frame / durationInFrames) * 100); return <AbsoluteFill style={{backgroundColor: \"#000\", display: \"flex\", justifyContent: \"center\", alignItems: \"center\", flexDirection: \"column\"}}><h1 style={{color: \"#fff\", fontSize: 200}}>{progress}%</h1><p style={{color: \"#666\", fontSize: 30}}>Loading...</p></AbsoluteFill>; }",
  "duration_seconds": 5,
  "fps": 60,
  "width": 1080,
  "height": 1080
}'
```

### Spring Animation

```bash
infsh app run infsh/remotion-render --input '{
  "code": "import { useCurrentFrame, useVideoConfig, spring, AbsoluteFill } from \"remotion\"; export default function Main() { const frame = useCurrentFrame(); const { fps } = useVideoConfig(); const scale = spring({ frame, fps, config: { damping: 10, stiffness: 100 } }); return <AbsoluteFill style={{backgroundColor: \"#6366f1\", display: \"flex\", justifyContent: \"center\", alignItems: \"center\"}}><div style={{width: 200, height: 200, backgroundColor: \"white\", borderRadius: 20, transform: `scale(${scale})`}} /></AbsoluteFill>; }",
  "duration_seconds": 2,
  "fps": 60,
  "width": 1080,
  "height": 1080
}'
```

### With Props

```bash
infsh app run infsh/remotion-render --input '{
  "code": "import { AbsoluteFill } from \"remotion\"; export default function Main({ title, subtitle }) { return <AbsoluteFill style={{backgroundColor: \"#000\", display: \"flex\", justifyContent: \"center\", alignItems: \"center\", flexDirection: \"column\"}}><h1 style={{color: \"#fff\", fontSize: 80}}>{title}</h1><p style={{color: \"#888\", fontSize: 40}}>{subtitle}</p></AbsoluteFill>; }",
  "props": {"title": "My Video", "subtitle": "Created with Remotion"},
  "duration_seconds": 3,
  "fps": 30,
  "width": 1920,
  "height": 1080
}'
```

### Sequence Animation

```bash
infsh app run infsh/remotion-render --input '{
  "code": "import { useCurrentFrame, AbsoluteFill, Sequence, interpolate } from \"remotion\"; function FadeIn({ children }) { const frame = useCurrentFrame(); const opacity = interpolate(frame, [0, 20], [0, 1]); return <div style={{ opacity }}>{children}</div>; } export default function Main() { return <AbsoluteFill style={{backgroundColor: \"#000\", display: \"flex\", justifyContent: \"center\", alignItems: \"center\", flexDirection: \"column\", gap: 20}}><Sequence from={0}><FadeIn><h1 style={{color: \"#fff\", fontSize: 60}}>First</h1></FadeIn></Sequence><Sequence from={30}><FadeIn><h1 style={{color: \"#fff\", fontSize: 60}}>Second</h1></FadeIn></Sequence><Sequence from={60}><FadeIn><h1 style={{color: \"#fff\", fontSize: 60}}>Third</h1></FadeIn></Sequence></AbsoluteFill>; }",
  "duration_seconds": 4,
  "fps": 30,
  "width": 1920,
  "height": 1080
}'
```

## Python SDK

```python
from inferencesh import inference

client = inference()

result = client.run({
    "app": "infsh/remotion-render",
    "input": {
        "code": """
import { useCurrentFrame, AbsoluteFill, interpolate } from "remotion";

export default function Main() {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 30], [0, 1]);

  return (
    <AbsoluteFill style={{
      backgroundColor: "#1a1a2e",
      display: "flex",
      justifyContent: "center",
      alignItems: "center"
    }}>
      <h1 style={{ color: "#eee", fontSize: 80, opacity }}>
        Hello from Python
      </h1>
    </AbsoluteFill>
  );
}
""",
        "duration_seconds": 3,
        "fps": 30,
        "width": 1920,
        "height": 1080
    }
})

print(result["output"]["video"])
```

### Streaming Progress

```python
for update in client.run({
    "app": "infsh/remotion-render",
    "input": {
        "code": "...",
        "duration_seconds": 10
    }
}, stream=True):
    if update.get("progress"):
        print(f"Rendering: {update['progress']}%")
    if update.get("output"):
        print(f"Video: {update['output']['video']}")
```

## Related Skills

```bash
# Remotion best practices (component patterns)
npx skills add remotion-dev/skills@remotion-best-practices

# AI video generation (for AI-generated clips)
npx skills add inference-sh/skills@ai-video-generation

# Image generation (for video assets)
npx skills add inference-sh/skills@ai-image-generation

# Python SDK reference
npx skills add inference-sh/skills@python-sdk

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

## Documentation

- [Remotion Documentation](https://www.remotion.dev/docs) - Official Remotion docs
- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Streaming Results](https://inference.sh/docs/api/sdk/streaming) - Real-time progress updates
