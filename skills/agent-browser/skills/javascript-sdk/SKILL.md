---
name: javascript-sdk
description: "JavaScript/TypeScript SDK for inference.sh - run AI apps, build agents, integrate 150+ models. Package: @inferencesh/sdk (npm install). Full TypeScript support, streaming, file uploads. Build agents with template or ad-hoc patterns, tool builder API, skills, human approval. Use for: JavaScript integration, TypeScript, Node.js, React, Next.js, frontend apps. Triggers: javascript sdk, typescript sdk, npm install, node.js api, js client, react ai, next.js ai, frontend sdk, @inferencesh/sdk, typescript agent, browser sdk, js integration"
allowed-tools: Bash(npm *), Bash(npx *), Bash(node *), Bash(pnpm *), Bash(yarn *)
---

# JavaScript SDK

Build AI applications with the [inference.sh](https://inference.sh) JavaScript/TypeScript SDK.

![JavaScript SDK](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kgvftjwhby36trvaj66bwzcf.jpeg)

## Quick Start

```bash
npm install @inferencesh/sdk
```

```typescript
import { inference } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_your_key' });

// Run an AI app
const result = await client.run({
  app: 'infsh/flux-schnell',
  input: { prompt: 'A sunset over mountains' }
});
console.log(result.output);
```

## Installation

```bash
npm install @inferencesh/sdk
# or
yarn add @inferencesh/sdk
# or
pnpm add @inferencesh/sdk
```

**Requirements:** Node.js 18.0.0+ (or modern browser with fetch)

## Authentication

```typescript
import { inference } from '@inferencesh/sdk';

// Direct API key
const client = inference({ apiKey: 'inf_your_key' });

// From environment variable (recommended)
const client = inference({ apiKey: process.env.INFERENCE_API_KEY });

// For frontend apps (use proxy)
const client = inference({ proxyUrl: '/api/inference/proxy' });
```

Get your API key: Settings → API Keys → Create API Key

## Running Apps

### Basic Execution

```typescript
const result = await client.run({
  app: 'infsh/flux-schnell',
  input: { prompt: 'A cat astronaut' }
});

console.log(result.status);  // "completed"
console.log(result.output);  // Output data
```

### Fire and Forget

```typescript
const task = await client.run({
  app: 'google/veo-3-1-fast',
  input: { prompt: 'Drone flying over mountains' }
}, { wait: false });

console.log(`Task ID: ${task.id}`);
// Check later with client.getTask(task.id)
```

### Streaming Progress

```typescript
const stream = await client.run({
  app: 'google/veo-3-1-fast',
  input: { prompt: 'Ocean waves at sunset' }
}, { stream: true });

for await (const update of stream) {
  console.log(`Status: ${update.status}`);
  if (update.logs?.length) {
    console.log(update.logs.at(-1));
  }
}
```

### Run Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `app` | string | App ID (namespace/name@version) |
| `input` | object | Input matching app schema |
| `setup` | object | Hidden setup configuration |
| `infra` | string | 'cloud' or 'private' |
| `session` | string | Session ID for stateful execution |
| `session_timeout` | number | Idle timeout (1-3600 seconds) |

## File Handling

### Automatic Upload

```typescript
const result = await client.run({
  app: 'image-processor',
  input: {
    image: '/path/to/image.png'  // Auto-uploaded
  }
});
```

### Manual Upload

```typescript
// Basic upload
const file = await client.uploadFile('/path/to/image.png');

// With options
const file = await client.uploadFile('/path/to/image.png', {
  filename: 'custom_name.png',
  contentType: 'image/png',
  public: true
});

const result = await client.run({
  app: 'image-processor',
  input: { image: file.uri }
});
```

### Browser File Upload

```typescript
const input = document.querySelector('input[type="file"]');
const file = await client.uploadFile(input.files[0]);
```

## Sessions (Stateful Execution)

Keep workers warm across multiple calls:

```typescript
// Start new session
const result = await client.run({
  app: 'my-app',
  input: { action: 'init' },
  session: 'new',
  session_timeout: 300  // 5 minutes
});
const sessionId = result.session_id;

// Continue in same session
const result2 = await client.run({
  app: 'my-app',
  input: { action: 'process' },
  session: sessionId
});
```

## Agent SDK

### Template Agents

Use pre-built agents from your workspace:

```typescript
const agent = client.agent('my-team/support-agent@latest');

// Send message
const response = await agent.sendMessage('Hello!');
console.log(response.text);

// Multi-turn conversation
const response2 = await agent.sendMessage('Tell me more');

// Reset conversation
agent.reset();

// Get chat history
const chat = await agent.getChat();
```

### Ad-hoc Agents

Create custom agents programmatically:

```typescript
import { tool, string, number, appTool } from '@inferencesh/sdk';

// Define tools
const calculator = tool('calculate')
  .describe('Perform a calculation')
  .param('expression', string('Math expression'))
  .build();

const imageGen = appTool('generate_image', 'infsh/flux-schnell@latest')
  .describe('Generate an image')
  .param('prompt', string('Image description'))
  .build();

// Create agent
const agent = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  system_prompt: 'You are a helpful assistant.',
  tools: [calculator, imageGen],
  temperature: 0.7,
  max_tokens: 4096
});

const response = await agent.sendMessage('What is 25 * 4?');
```

### Available Core Apps

| Model | App Reference |
|-------|---------------|
| Claude Sonnet 4 | `infsh/claude-sonnet-4@latest` |
| Claude 3.5 Haiku | `infsh/claude-haiku-35@latest` |
| GPT-4o | `infsh/gpt-4o@latest` |
| GPT-4o Mini | `infsh/gpt-4o-mini@latest` |

## Tool Builder API

### Parameter Types

```typescript
import {
  string, number, integer, boolean,
  enumOf, array, obj, optional
} from '@inferencesh/sdk';

const name = string('User\'s name');
const age = integer('Age in years');
const score = number('Score 0-1');
const active = boolean('Is active');
const priority = enumOf(['low', 'medium', 'high'], 'Priority');
const tags = array(string('Tag'), 'List of tags');
const address = obj({
  street: string('Street'),
  city: string('City'),
  zip: optional(string('ZIP'))
}, 'Address');
```

### Client Tools (Run in Your Code)

```typescript
const greet = tool('greet')
  .display('Greet User')
  .describe('Greets a user by name')
  .param('name', string('Name to greet'))
  .requireApproval()
  .build();
```

### App Tools (Call AI Apps)

```typescript
const generate = appTool('generate_image', 'infsh/flux-schnell@latest')
  .describe('Generate an image from text')
  .param('prompt', string('Image description'))
  .setup({ model: 'schnell' })
  .input({ steps: 20 })
  .requireApproval()
  .build();
```

### Agent Tools (Delegate to Sub-agents)

```typescript
import { agentTool } from '@inferencesh/sdk';

const researcher = agentTool('research', 'my-org/researcher@v1')
  .describe('Research a topic')
  .param('topic', string('Topic to research'))
  .build();
```

### Webhook Tools (Call External APIs)

```typescript
import { webhookTool } from '@inferencesh/sdk';

const notify = webhookTool('slack', 'https://hooks.slack.com/...')
  .describe('Send Slack notification')
  .secret('SLACK_SECRET')
  .param('channel', string('Channel'))
  .param('message', string('Message'))
  .build();
```

### Internal Tools (Built-in Capabilities)

```typescript
import { internalTools } from '@inferencesh/sdk';

const config = internalTools()
  .plan()
  .memory()
  .webSearch(true)
  .codeExecution(true)
  .imageGeneration({
    enabled: true,
    appRef: 'infsh/flux@latest'
  })
  .build();

const agent = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  internal_tools: config
});
```

## Streaming Agent Responses

```typescript
const response = await agent.sendMessage('Explain quantum computing', {
  onMessage: (msg) => {
    if (msg.content) {
      process.stdout.write(msg.content);
    }
  },
  onToolCall: async (call) => {
    console.log(`\n[Tool: ${call.name}]`);
    const result = await executeTool(call.name, call.args);
    agent.submitToolResult(call.id, result);
  }
});
```

## File Attachments

```typescript
// From file path (Node.js)
import { readFileSync } from 'fs';
const response = await agent.sendMessage('What\'s in this image?', {
  files: [readFileSync('image.png')]
});

// From base64
const response = await agent.sendMessage('Analyze this', {
  files: ['data:image/png;base64,iVBORw0KGgo...']
});

// From browser File object
const input = document.querySelector('input[type="file"]');
const response = await agent.sendMessage('Describe this', {
  files: [input.files[0]]
});
```

## Skills (Reusable Context)

```typescript
const agent = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  skills: [
    {
      name: 'code-review',
      description: 'Code review guidelines',
      content: '# Code Review\n\n1. Check security\n2. Check performance...'
    },
    {
      name: 'api-docs',
      description: 'API documentation',
      url: 'https://example.com/skills/api-docs.md'
    }
  ]
});
```

## Server Proxy (Frontend Apps)

For browser apps, proxy through your backend to keep API keys secure:

### Client Setup

```typescript
const client = inference({
  proxyUrl: '/api/inference/proxy'
  // No apiKey needed on frontend
});
```

### Next.js Proxy (App Router)

```typescript
// app/api/inference/proxy/route.ts
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY
});

export const POST = route.POST;
```

### Express Proxy

```typescript
import express from 'express';
import { createProxyMiddleware } from '@inferencesh/sdk/proxy/express';

const app = express();
app.use('/api/inference/proxy', createProxyMiddleware({
  apiKey: process.env.INFERENCE_API_KEY
}));
```

### Supported Frameworks

- Next.js (App Router & Pages Router)
- Express
- Hono
- Remix
- SvelteKit

## TypeScript Support

Full type definitions included:

```typescript
import type {
  TaskDTO,
  ChatDTO,
  ChatMessageDTO,
  AgentTool,
  TaskStatusCompleted,
  TaskStatusFailed
} from '@inferencesh/sdk';

if (result.status === TaskStatusCompleted) {
  console.log('Done!');
} else if (result.status === TaskStatusFailed) {
  console.log('Failed:', result.error);
}
```

## Error Handling

```typescript
import { RequirementsNotMetException, InferenceError } from '@inferencesh/sdk';

try {
  const result = await client.run({ app: 'my-app', input: {...} });
} catch (e) {
  if (e instanceof RequirementsNotMetException) {
    console.log('Missing requirements:');
    for (const err of e.errors) {
      console.log(`  - ${err.type}: ${err.key}`);
    }
  } else if (e instanceof InferenceError) {
    console.log('API error:', e.message);
  }
}
```

## Human Approval Workflows

```typescript
const response = await agent.sendMessage('Delete all temp files', {
  onToolCall: async (call) => {
    if (call.requiresApproval) {
      const approved = await promptUser(`Allow ${call.name}?`);
      if (approved) {
        const result = await executeTool(call.name, call.args);
        agent.submitToolResult(call.id, result);
      } else {
        agent.submitToolResult(call.id, { error: 'Denied by user' });
      }
    }
  }
});
```

## CommonJS Support

```javascript
const { inference, tool, string } = require('@inferencesh/sdk');

const client = inference({ apiKey: 'inf_...' });
const result = await client.run({...});
```

## Reference Files

- [Agent Patterns](references/agent-patterns.md) - Multi-agent, RAG, batch processing patterns
- [Tool Builder](references/tool-builder.md) - Complete tool builder API reference
- [Server Proxy](references/server-proxy.md) - Next.js, Express, Hono, Remix, SvelteKit setup
- [Streaming](references/streaming.md) - Real-time progress updates and SSE handling
- [File Handling](references/files.md) - Upload, download, and manage files
- [Sessions](references/sessions.md) - Stateful execution with warm workers
- [TypeScript](references/typescript.md) - Type definitions and type-safe patterns
- [React Integration](references/react-integration.md) - Hooks, components, and patterns

## Related Skills

```bash
# Python SDK
npx skills add inference-sh/skills@python-sdk

# Full platform skill (all 150+ apps via CLI)
npx skills add inference-sh/skills@inference-sh

# LLM models
npx skills add inference-sh/skills@llm-models

# Image generation
npx skills add inference-sh/skills@ai-image-generation
```

## Documentation

- [JavaScript SDK Reference](https://inference.sh/docs/api/sdk-javascript) - Full API documentation
- [Agent SDK Overview](https://inference.sh/docs/api/agent-sdk) - Building agents
- [Tool Builder Reference](https://inference.sh/docs/api/agent-tools) - Creating tools
- [Server Proxy Setup](https://inference.sh/docs/api/sdk/server-proxy) - Frontend integration
- [Authentication](https://inference.sh/docs/api/authentication) - API key setup
- [Streaming](https://inference.sh/docs/api/sdk/streaming) - Real-time updates
- [File Uploads](https://inference.sh/docs/api/sdk/files) - File handling
