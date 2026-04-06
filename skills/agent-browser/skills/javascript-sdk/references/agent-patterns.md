# Agent Patterns

Common patterns for building agents with the JavaScript SDK.

## Multi-Agent Orchestration

Delegate tasks to specialized sub-agents:

```typescript
import { inference, agentTool, string } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

// Define sub-agents as tools
const researcher = agentTool('research', 'my-org/researcher@latest')
  .describe('Research a topic thoroughly')
  .param('topic', string('Topic to research'))
  .build();

const writer = agentTool('write', 'my-org/writer@latest')
  .describe('Write content based on research')
  .param('outline', string('Content outline'))
  .param('research', string('Research findings'))
  .build();

// Orchestrator agent
const orchestrator = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  system_prompt: `You are an orchestrator that:
1. Uses the research tool to gather information
2. Uses the write tool to create content
Coordinate between agents to produce high-quality output.`,
  tools: [researcher, writer]
});

const response = await orchestrator.sendMessage('Create a blog post about AI agents');
```

## RAG Pattern (Retrieval-Augmented Generation)

Combine search with LLM responses:

```typescript
import { inference, appTool, string } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

// Search tool
const search = appTool('search', 'tavily/search-assistant@latest')
  .describe('Search the web for current information')
  .param('query', string('Search query'))
  .build();

// RAG agent
const ragAgent = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  system_prompt: `You help users with current information.
When asked about recent events or facts you're unsure about,
use the search tool to find accurate, up-to-date information.
Always cite your sources.`,
  tools: [search]
});

const response = await ragAgent.sendMessage(
  'What are the latest developments in quantum computing?'
);
```

## Code Execution Pattern

Agents that can write and run code:

```typescript
import { inference, internalTools } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

const config = internalTools()
  .codeExecution(true)
  .build();

const coder = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  system_prompt: `You are a coding assistant.
Write code to solve problems and execute it to verify it works.
Explain your approach and show the output.`,
  internal_tools: config
});

const response = await coder.sendMessage('Calculate the first 20 Fibonacci numbers');
```

## Human-in-the-Loop Pattern

Require approval for sensitive operations:

```typescript
import { inference, tool, string } from '@inferencesh/sdk';
import * as readline from 'readline';

const client = inference({ apiKey: 'inf_...' });

// Tool requiring approval
const deleteFile = tool('delete_file')
  .describe('Delete a file from the filesystem')
  .param('path', string('File path to delete'))
  .requireApproval()
  .build();

async function promptUser(question: string): Promise<boolean> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
  });

  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.toLowerCase() === 'y');
    });
  });
}

const agent = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  tools: [deleteFile]
});

const response = await agent.sendMessage('Clean up temporary files in /tmp/myapp', {
  onToolCall: async (call) => {
    if (call.requiresApproval) {
      console.log(`\n⚠️  Agent wants to: ${call.name}`);
      console.log(`   Arguments: ${JSON.stringify(call.args)}`);
      const approved = await promptUser('Allow? (y/n): ');

      if (approved) {
        const result = await executeOperation(call.name, call.args);
        agent.submitToolResult(call.id, result);
      } else {
        agent.submitToolResult(call.id, { error: 'Operation denied by user' });
      }
    }
  }
});
```

## Conversation Memory Pattern

Maintain context across sessions:

```typescript
import { inference } from '@inferencesh/sdk';
import { readFileSync, writeFileSync, existsSync } from 'fs';

const client = inference({ apiKey: 'inf_...' });

function saveChat(agent: any, filepath: string) {
  const chat = agent.getChat();
  writeFileSync(filepath, JSON.stringify(chat, null, 2));
}

async function loadAndContinue(filepath: string) {
  const agent = client.agent('my-org/assistant@latest');

  if (existsSync(filepath)) {
    const chat = JSON.parse(readFileSync(filepath, 'utf-8'));
    // Restore by replaying user messages
    for (const msg of chat.messages) {
      if (msg.role === 'user') {
        await agent.sendMessage(msg.content);
      }
    }
  }

  return agent;
}

// Usage
const agent = await loadAndContinue('conversation.json');
const response = await agent.sendMessage('Continue where we left off');
saveChat(agent, 'conversation.json');
```

## Streaming with Progress UI

Real-time updates for better UX:

```typescript
import { inference } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });
const agent = client.agent('my-org/assistant@latest');

const response = await agent.sendMessage('Generate a report on market trends', {
  onMessage: (msg) => {
    if (msg.content) {
      process.stdout.write(msg.content);
    }
  },
  onToolCall: async (call) => {
    console.log(`\n🔧 Using tool: ${call.name}`);
    const result = await executeTool(call.name, call.args);
    agent.submitToolResult(call.id, result);
    console.log('✅ Tool completed');
  }
});

console.log('\n\n📊 Report complete!');
```

## React Integration Pattern

Use in React components:

```typescript
import { useState, useCallback } from 'react';
import { inference } from '@inferencesh/sdk';

const client = inference({ proxyUrl: '/api/inference/proxy' });

function ChatComponent() {
  const [messages, setMessages] = useState<string[]>([]);
  const [input, setInput] = useState('');
  const [agent] = useState(() => client.agent('my-org/assistant@latest'));

  const sendMessage = useCallback(async () => {
    if (!input.trim()) return;

    setMessages(prev => [...prev, `You: ${input}`]);
    setInput('');

    let response = '';
    await agent.sendMessage(input, {
      onMessage: (msg) => {
        if (msg.content) {
          response += msg.content;
          setMessages(prev => [
            ...prev.slice(0, -1),
            `Assistant: ${response}`
          ]);
        }
      }
    });
  }, [input, agent]);

  return (
    <div>
      {messages.map((msg, i) => <div key={i}>{msg}</div>)}
      <input value={input} onChange={e => setInput(e.target.value)} />
      <button onClick={sendMessage}>Send</button>
    </div>
  );
}
```

## Error Recovery Pattern

Graceful handling of failures:

```typescript
import { inference, RequirementsNotMetException, InferenceError } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

async function robustRun(config: any, maxRetries = 3) {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await client.run(config);
    } catch (e) {
      if (e instanceof RequirementsNotMetException) {
        console.log('Missing requirements:', e.errors);
        throw e;
      }
      if (e instanceof InferenceError && attempt < maxRetries - 1) {
        const wait = Math.pow(2, attempt) * 1000;
        console.log(`Error: ${e.message}. Retrying in ${wait}ms...`);
        await new Promise(r => setTimeout(r, wait));
      } else {
        throw e;
      }
    }
  }
}

const result = await robustRun({
  app: 'infsh/flux-schnell',
  input: { prompt: 'A serene landscape' }
});
```

## Batch Processing Pattern

Process multiple items efficiently:

```typescript
import { inference } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

async function processBatch(items: string[], concurrency = 5) {
  const results: any[] = [];
  const queue = [...items];
  const inProgress: Promise<void>[] = [];

  async function processOne(item: string) {
    const result = await client.run({
      app: 'infsh/flux-schnell',
      input: { prompt: item }
    });
    results.push(result);
  }

  while (queue.length > 0 || inProgress.length > 0) {
    // Fill up to concurrency limit
    while (queue.length > 0 && inProgress.length < concurrency) {
      const item = queue.shift()!;
      const promise = processOne(item).then(() => {
        const index = inProgress.indexOf(promise);
        if (index > -1) inProgress.splice(index, 1);
      });
      inProgress.push(promise);
    }

    // Wait for at least one to complete
    if (inProgress.length > 0) {
      await Promise.race(inProgress);
    }
  }

  return results;
}

const prompts = [
  'A mountain sunrise',
  'A city at night',
  'An ocean sunset',
  'A forest path'
];

const results = await processBatch(prompts);
```

## Next.js Server Actions Pattern

Use with React Server Components:

```typescript
// app/actions.ts
'use server';

import { inference } from '@inferencesh/sdk';

const client = inference({ apiKey: process.env.INFERENCE_API_KEY });

export async function generateImage(prompt: string) {
  const result = await client.run({
    app: 'infsh/flux-schnell',
    input: { prompt }
  });
  return result.output;
}

export async function chat(message: string, sessionId?: string) {
  const agent = client.agent('my-org/assistant@latest');
  const response = await agent.sendMessage(message);
  return {
    text: response.text,
    sessionId: response.sessionId
  };
}
```

```typescript
// app/page.tsx
import { generateImage, chat } from './actions';

export default function Page() {
  async function handleSubmit(formData: FormData) {
    'use server';
    const prompt = formData.get('prompt') as string;
    const image = await generateImage(prompt);
    // Handle result
  }

  return (
    <form action={handleSubmit}>
      <input name="prompt" placeholder="Describe an image..." />
      <button type="submit">Generate</button>
    </form>
  );
}
```
