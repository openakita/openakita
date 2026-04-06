# TypeScript Reference

Full type definitions and type-safe patterns.

## Installation

TypeScript definitions are included with the SDK:

```bash
npm install @inferencesh/sdk
```

No additional `@types` package needed.

## Basic Types

```typescript
import {
  inference,
  type TaskDTO,
  type ChatDTO,
  type ChatMessageDTO,
  type AgentTool,
  type TaskStatus
} from '@inferencesh/sdk';
```

## Task Types

```typescript
import type {
  TaskDTO,
  TaskStatus,
  TaskStatusCompleted,
  TaskStatusFailed,
  TaskStatusRunning,
  TaskOutput
} from '@inferencesh/sdk';

// Check task status
function handleTask(task: TaskDTO) {
  if (task.status === 'completed') {
    console.log('Output:', task.output);
  } else if (task.status === 'failed') {
    console.log('Error:', task.error);
  } else if (task.status === 'running') {
    console.log('Progress:', task.progress);
  }
}
```

## Run Options Types

```typescript
import type { RunOptions, StreamOptions } from '@inferencesh/sdk';

const options: RunOptions = {
  wait: true,
  stream: false
};

const streamOptions: StreamOptions = {
  stream: true,
  signal: new AbortController().signal
};
```

## Agent Types

```typescript
import type {
  Agent,
  AgentConfig,
  AgentMessage,
  AgentToolCall,
  AgentResponse
} from '@inferencesh/sdk';

// Type-safe agent config
const config: AgentConfig = {
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  system_prompt: 'You are a helpful assistant.',
  tools: [],
  temperature: 0.7,
  max_tokens: 4096
};

// Type-safe message handler
function handleMessage(msg: AgentMessage) {
  if (msg.type === 'text') {
    console.log(msg.content);
  } else if (msg.type === 'tool_use') {
    console.log('Tool:', msg.tool_name);
  }
}
```

## Tool Types

```typescript
import {
  tool, string, number, integer, boolean, enumOf, array, obj, optional,
  type AgentTool,
  type ParamType,
  type ToolCall
} from '@inferencesh/sdk';

// Type-safe tool definition
const myTool: AgentTool = tool('my_tool')
  .describe('Does something')
  .param('input', string('Input value'))
  .build();

// Type-safe tool call handler
function handleToolCall(call: ToolCall) {
  const { id, name, args } = call;
  console.log(`Tool ${name} called with:`, args);
}
```

## Parameter Type Helpers

```typescript
import type { ParamType, StringParam, NumberParam, ObjectParam } from '@inferencesh/sdk';

// String parameter
const nameParam: ParamType = string('User name');

// Number parameter
const scoreParam: ParamType = number('Score value');

// Object parameter
const addressParam: ObjectParam = obj({
  street: string('Street'),
  city: string('City'),
  zip: optional(string('ZIP'))
}, 'Address');
```

## Generic Client

```typescript
import { inference, type InferenceClient } from '@inferencesh/sdk';

const client: InferenceClient = inference({ apiKey: 'inf_...' });

// All methods are typed
const result = await client.run({
  app: 'my-app',
  input: { data: 'test' }
});
// result is TaskDTO
```

## Custom Input/Output Types

```typescript
interface MyAppInput {
  prompt: string;
  style?: 'realistic' | 'artistic' | 'cartoon';
  steps?: number;
}

interface MyAppOutput {
  image: string;
  metadata: {
    seed: number;
    duration_ms: number;
  };
}

async function generateImage(input: MyAppInput): Promise<MyAppOutput> {
  const result = await client.run({
    app: 'infsh/flux-schnell',
    input
  });

  return result.output as MyAppOutput;
}
```

## Type Guards

```typescript
import type { TaskDTO, TaskStatus } from '@inferencesh/sdk';

function isCompleted(task: TaskDTO): task is TaskDTO & { status: 'completed' } {
  return task.status === 'completed';
}

function isFailed(task: TaskDTO): task is TaskDTO & { status: 'failed' } {
  return task.status === 'failed';
}

// Usage
const task = await client.run({ app: 'my-app', input: {} });

if (isCompleted(task)) {
  // TypeScript knows task.output exists here
  console.log(task.output);
} else if (isFailed(task)) {
  // TypeScript knows task.error exists here
  console.log(task.error);
}
```

## Error Types

```typescript
import {
  InferenceError,
  RequirementsNotMetException,
  type RequirementError
} from '@inferencesh/sdk';

try {
  await client.run({ app: 'my-app', input: {} });
} catch (e) {
  if (e instanceof RequirementsNotMetException) {
    const errors: RequirementError[] = e.errors;
    for (const err of errors) {
      console.log(`${err.type}: ${err.key}`);
    }
  } else if (e instanceof InferenceError) {
    console.log('API error:', e.message);
  }
}
```

## File Types

```typescript
import type { FileDTO, UploadOptions } from '@inferencesh/sdk';

const options: UploadOptions = {
  filename: 'image.png',
  contentType: 'image/png',
  public: true
};

const file: FileDTO = await client.uploadFile('/path/to/file', options);
console.log(file.uri, file.url, file.size);
```

## Stream Types

```typescript
import type { StreamUpdate, StreamOptions } from '@inferencesh/sdk';

async function* typedStream(config: any): AsyncGenerator<StreamUpdate> {
  const stream = await client.run(config, { stream: true });
  yield* stream;
}

// Usage
for await (const update of typedStream({ app: 'my-app', input: {} })) {
  // update is StreamUpdate
  console.log(update.status);
}
```

## React Integration Types

```typescript
import type { Agent, AgentMessage, ToolCall } from '@inferencesh/sdk';

interface ChatState {
  messages: AgentMessage[];
  loading: boolean;
  error: string | null;
}

interface ChatActions {
  sendMessage: (text: string) => Promise<void>;
  reset: () => void;
}

function useChat(agentRef: string): ChatState & ChatActions {
  // Implementation
}
```

## Strict Mode Configuration

For strictest type checking, use these tsconfig options:

```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true
  }
}
```

## Module Augmentation

Extend types for custom use cases:

```typescript
import '@inferencesh/sdk';

declare module '@inferencesh/sdk' {
  interface TaskDTO {
    customField?: string;
  }
}
```

## JSDoc Type Annotations

For JavaScript projects wanting type hints:

```javascript
/** @type {import('@inferencesh/sdk').InferenceClient} */
const client = inference({ apiKey: 'inf_...' });

/**
 * @param {import('@inferencesh/sdk').TaskDTO} task
 */
function handleTask(task) {
  console.log(task.status);
}
```

## Zod Integration

Validate runtime types with Zod:

```typescript
import { z } from 'zod';

const ImageOutputSchema = z.object({
  image: z.string().url(),
  metadata: z.object({
    seed: z.number(),
    duration_ms: z.number()
  })
});

type ImageOutput = z.infer<typeof ImageOutputSchema>;

async function generateImage(prompt: string): Promise<ImageOutput> {
  const result = await client.run({
    app: 'infsh/flux-schnell',
    input: { prompt }
  });

  // Validate at runtime
  return ImageOutputSchema.parse(result.output);
}
```

## Full Example

```typescript
import {
  inference,
  tool,
  appTool,
  string,
  enumOf,
  type InferenceClient,
  type AgentTool,
  type TaskDTO,
  type ToolCall
} from '@inferencesh/sdk';

// Typed client
const client: InferenceClient = inference({
  apiKey: process.env.INFERENCE_API_KEY!
});

// Typed tools
const tools: AgentTool[] = [
  tool('search')
    .describe('Search for information')
    .param('query', string('Search query'))
    .build(),

  appTool('generate', 'infsh/flux-schnell@latest')
    .describe('Generate image')
    .param('prompt', string('Description'))
    .param('style', enumOf(['realistic', 'artistic'], 'Style'))
    .build()
];

// Typed agent
const agent = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  system_prompt: 'You are helpful.',
  tools
});

// Typed handler
async function handleToolCall(call: ToolCall): Promise<void> {
  console.log(`Tool: ${call.name}, Args:`, call.args);
}

// Typed response handling
const response = await agent.sendMessage('Hello', {
  onToolCall: handleToolCall
});

console.log(response.text);
```
