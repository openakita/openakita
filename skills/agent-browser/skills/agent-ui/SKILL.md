---
name: agent-ui
description: "Batteries-included agent component for React/Next.js from ui.inference.sh. One component with runtime, tools, streaming, approvals, and widgets built in. Capabilities: drop-in agent, human-in-the-loop, client-side tools, form filling. Use for: building AI chat interfaces, agentic UIs, SaaS copilots, assistants. Triggers: agent component, agent ui, chat agent, shadcn agent, react agent,"  agentic ui, ai assistant ui, copilot ui, inference ui, human in the loop
---

# Agent Component

Batteries-included agent component from [ui.inference.sh](https://ui.inference.sh).

![Agent Component](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kgvftp7hb8wby7z66fvs9asd.jpeg)

## Quick Start

```bash
# Install the agent component
npx shadcn@latest add https://ui.inference.sh/r/agent.json

# Add the SDK for the proxy route
npm install @inferencesh/sdk
```

## Setup

### 1. API Proxy Route (Next.js)

```typescript
// app/api/inference/proxy/route.ts
import { route } from '@inferencesh/sdk/proxy/nextjs';
export const { GET, POST, PUT } = route;
```

### 2. Environment Variable

```bash
# .env.local
INFERENCE_API_KEY=inf_...
```

### 3. Use the Component

```tsx
import { Agent } from "@/registry/blocks/agent/agent"

export default function Page() {
  return (
    <Agent
      proxyUrl="/api/inference/proxy"
      agentConfig={{
        core_app: { ref: 'openrouter/claude-haiku-45@0fkg6xwb' },
        description: 'a helpful ai assistant',
        system_prompt: 'you are helpful.',
      }}
    />
  )
}
```

## Features

| Feature | Description |
|---------|-------------|
| Runtime included | No backend logic needed |
| Tool lifecycle | Pending, progress, approval, results |
| Human-in-the-loop | Built-in approval flows |
| Widgets | Declarative JSON UI from agent responses |
| Streaming | Real-time token streaming |
| Client-side tools | Tools that run in the browser |

## Client-Side Tools Example

```tsx
import { Agent } from "@/registry/blocks/agent/agent"
import { createScopedTools } from "./blocks/agent/lib/client-tools"

const formRef = useRef<HTMLFormElement>(null)
const scopedTools = createScopedTools(formRef)

<Agent
  proxyUrl="/api/inference/proxy"
  config={{
    core_app: { ref: 'openrouter/claude-haiku-45@0fkg6xwb' },
    tools: scopedTools,
    system_prompt: 'You can fill forms using scan_ui and fill_field tools.',
  }}
/>
```

## Props

| Prop | Type | Description |
|------|------|-------------|
| `proxyUrl` | string | API proxy endpoint |
| `name` | string | Agent name (optional) |
| `config` | AgentConfig | Agent configuration |
| `allowFiles` | boolean | Enable file uploads |
| `allowImages` | boolean | Enable image uploads |

## Related Skills

```bash
# Chat UI building blocks
npx skills add inference-sh/skills@chat-ui

# Declarative widgets from JSON
npx skills add inference-sh/skills@widgets-ui

# Tool lifecycle UI
npx skills add inference-sh/skills@tools-ui
```

## Documentation

- [Agents Overview](https://inference.sh/docs/agents/overview) - Building AI agents
- [Agent SDK](https://inference.sh/docs/api/agent/overview) - Programmatic agent control
- [Human-in-the-Loop](https://inference.sh/docs/runtime/human-in-the-loop) - Approval flows
- [Agents That Generate UI](https://inference.sh/blog/ux/generative-ui) - Building generative UIs
- [Agent UX Patterns](https://inference.sh/blog/ux/agent-ux-patterns) - Best practices

Component docs: [ui.inference.sh/blocks/agent](https://ui.inference.sh/blocks/agent)
