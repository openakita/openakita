# Tool Builder Reference

Complete guide to building tools with the JavaScript SDK.

## Parameter Types

### Basic Types

```typescript
import { string, number, integer, boolean } from '@inferencesh/sdk';

// String parameter
const name = string('The user\'s full name');

// Number (float)
const score = number('Score between 0 and 1');

// Integer
const count = integer('Number of items');

// Boolean
const enabled = boolean('Whether feature is enabled');
```

### Enum Type

```typescript
import { enumOf } from '@inferencesh/sdk';

const priority = enumOf(
  ['low', 'medium', 'high', 'critical'],
  'Task priority level'
);
```

### Array Type

```typescript
import { array, string, obj, integer } from '@inferencesh/sdk';

// Array of strings
const tags = array(string('Tag name'), 'List of tags');

// Array of objects
const items = array(
  obj({
    name: string('Item name'),
    qty: integer('Quantity')
  }),
  'List of items'
);
```

### Object Type

```typescript
import { obj, string, integer, optional } from '@inferencesh/sdk';

const address = obj({
  street: string('Street address'),
  city: string('City name'),
  state: string('State code'),
  zip: optional(string('ZIP code'))
}, 'Mailing address');
```

### Optional Parameters

```typescript
import { optional, string } from '@inferencesh/sdk';

// Optional string
const nickname = optional(string('User\'s nickname'));
```

## Tool Types

### Client Tools

Tools that execute in your code:

```typescript
import { tool, string, integer } from '@inferencesh/sdk';

// Basic tool
const greet = tool('greet')
  .describe('Greets a user')
  .param('name', string('Name to greet'))
  .build();

// Tool with multiple parameters
const sendEmail = tool('send_email')
  .display('Send Email')
  .describe('Sends an email to a recipient')
  .param('to', string('Recipient email'))
  .param('subject', string('Email subject'))
  .param('body', string('Email body'))
  .param('priority', integer('Priority 1-5'), 3) // default value
  .requireApproval()
  .build();
```

### App Tools

Tools that call inference.sh apps:

```typescript
import { appTool, string } from '@inferencesh/sdk';

// Basic app tool
const generate = appTool('generate_image', 'infsh/flux-schnell@latest')
  .describe('Generate an image from a text prompt')
  .param('prompt', string('Image description'))
  .build();

// App tool with setup and defaults
const translate = appTool('translate', 'infsh/translator@latest')
  .describe('Translate text between languages')
  .param('text', string('Text to translate'))
  .param('targetLang', string('Target language code'))
  .setup({
    model: 'advanced',
    preserveFormatting: true
  })
  .input({
    sourceLang: 'auto'
  })
  .build();
```

### Agent Tools

Tools that delegate to other agents:

```typescript
import { agentTool, string } from '@inferencesh/sdk';

const researcher = agentTool('research', 'my-org/researcher@v1')
  .describe('Research a topic in depth')
  .param('topic', string('Topic to research'))
  .param('depth', string('Research depth: brief, moderate, comprehensive'))
  .build();

const coder = agentTool('write_code', 'my-org/coder@latest')
  .describe('Write code to solve a problem')
  .param('task', string('Coding task description'))
  .param('language', string('Programming language'))
  .build();
```

### Webhook Tools

Tools that call external HTTP endpoints:

```typescript
import { webhookTool, string } from '@inferencesh/sdk';

// Slack notification
const slack = webhookTool('notify_slack', 'https://hooks.slack.com/services/...')
  .describe('Send a message to Slack')
  .param('channel', string('Channel name'))
  .param('message', string('Message text'))
  .build();

// Webhook with secret
const github = webhookTool('create_issue', 'https://api.github.com/repos/org/repo/issues')
  .describe('Create a GitHub issue')
  .secret('GITHUB_TOKEN') // Uses stored secret
  .param('title', string('Issue title'))
  .param('body', string('Issue description'))
  .build();
```

## Tool Builder Methods

### Common Methods

| Method | Description |
|--------|-------------|
| `.describe(text)` | Set tool description |
| `.display(name)` | Set display name |
| `.param(name, type, default?)` | Add parameter |
| `.requireApproval()` | Require human approval |
| `.build()` | Build the tool |

### App Tool Methods

| Method | Description |
|--------|-------------|
| `.setup(config)` | Hidden setup configuration |
| `.input(defaults)` | Default input values |

### Webhook Tool Methods

| Method | Description |
|--------|-------------|
| `.secret(name)` | Use stored secret for auth |

## Internal Tools

Built-in capabilities you can enable:

```typescript
import { internalTools } from '@inferencesh/sdk';

const config = internalTools()
  .plan()                    // Task planning
  .memory()                  // Information storage
  .webSearch(true)          // Web search capability
  .codeExecution(true)      // Run code
  .imageGeneration({
    enabled: true,
    appRef: 'infsh/flux@latest'
  })
  .build();
```

### Internal Tool Options

| Tool | Description |
|------|-------------|
| `.plan()` | Enable task breakdown and planning |
| `.memory()` | Enable information storage |
| `.webSearch(enabled)` | Enable/disable web search |
| `.codeExecution(enabled)` | Enable/disable code running |
| `.imageGeneration(config)` | Configure image generation |

## Handling Tool Calls

### Basic Handler

```typescript
const response = await agent.sendMessage('Greet John', {
  onToolCall: async (call) => {
    let result;

    if (call.name === 'greet') {
      result = `Hello, ${call.args.name}!`;
    } else if (call.name === 'calculate') {
      result = eval(call.args.expression);
    } else {
      result = { error: `Unknown tool: ${call.name}` };
    }

    agent.submitToolResult(call.id, result);
  }
});
```

### With Approval

```typescript
import * as readline from 'readline';

async function promptUser(question: string): Promise<boolean> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
  });
  return new Promise(resolve => {
    rl.question(question, answer => {
      rl.close();
      resolve(answer.toLowerCase() === 'y');
    });
  });
}

const response = await agent.sendMessage('Delete temp files', {
  onToolCall: async (call) => {
    if (call.requiresApproval) {
      console.log(`Tool: ${call.name}`);
      console.log(`Args: ${JSON.stringify(call.args)}`);
      const approved = await promptUser('Approve? (y/n): ');

      if (!approved) {
        agent.submitToolResult(call.id, { error: 'Denied by user' });
        return;
      }
    }

    const result = await executeTool(call.name, call.args);
    agent.submitToolResult(call.id, result);
  }
});
```

### Widget Results

Return structured data for UI widgets:

```typescript
const response = await agent.sendMessage('Show order confirmation', {
  onToolCall: async (call) => {
    if (call.name === 'confirm_order') {
      // Return widget data
      agent.submitToolResult(call.id, {
        action: { type: 'confirm' },
        formData: {
          orderId: '12345',
          items: ['Widget A', 'Widget B'],
          total: 99.99
        }
      });
    }
  }
});
```

## TypeScript Types

```typescript
import type {
  AgentTool,
  ToolCall,
  ToolResult,
  ParamType
} from '@inferencesh/sdk';

// Type-safe tool definition
const typedTool: AgentTool = tool('my_tool')
  .describe('A typed tool')
  .param('input', string('Input value'))
  .build();

// Type-safe handler
function handleToolCall(call: ToolCall): ToolResult {
  return { success: true, data: call.args };
}
```

## Complete Example

```typescript
import {
  inference, tool, appTool, webhookTool,
  string, number, integer, boolean, enumOf,
  array, obj, optional, internalTools
} from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

// Calculator tool
const calculator = tool('calculate')
  .display('Calculator')
  .describe('Perform mathematical calculations')
  .param('expression', string('Math expression to evaluate'))
  .build();

// Image generation tool
const imageGen = appTool('generate_image', 'infsh/flux-schnell@latest')
  .describe('Generate an image from text')
  .param('prompt', string('Image description'))
  .param('style', enumOf(['realistic', 'artistic', 'cartoon'], 'Image style'))
  .setup({ quality: 'high' })
  .input({ steps: 20 })
  .requireApproval()
  .build();

// Slack notification tool
const slack = webhookTool('notify', 'https://hooks.slack.com/...')
  .describe('Send Slack notification')
  .param('message', string('Message to send'))
  .build();

// Built-in tools
const internals = internalTools()
  .webSearch(true)
  .codeExecution(true)
  .build();

// Create agent with all tools
const agent = client.agent({
  core_app: { ref: 'infsh/claude-sonnet-4@latest' },
  system_prompt: 'You are a helpful assistant with various capabilities.',
  tools: [calculator, imageGen, slack],
  internal_tools: internals,
  temperature: 0.7
});

// Handle tool calls
const response = await agent.sendMessage(
  'Calculate 15% tip on $85, then notify Slack',
  {
    onToolCall: async (call) => {
      if (call.name === 'calculate') {
        try {
          const result = eval(call.args.expression);
          agent.submitToolResult(call.id, { result });
        } catch (e) {
          agent.submitToolResult(call.id, { error: String(e) });
        }
      } else if (call.requiresApproval) {
        const approved = await promptUser(`Allow ${call.name}? (y/n): `);
        if (!approved) {
          agent.submitToolResult(call.id, { error: 'Denied' });
        }
        // App/webhook tools execute automatically if approved
      }
    }
  }
);
```

## Server Proxy Tools

When using the SDK in the browser, tools that require API keys should use webhooks with secrets stored server-side:

```typescript
// Browser-safe tool definition
const apiTool = webhookTool('call_api', '/api/my-endpoint')
  .describe('Call server-side API')
  .param('data', obj({
    action: string('Action to perform'),
    payload: optional(string('Optional payload'))
  }))
  .build();

// Server-side handler (e.g., Next.js API route)
// app/api/my-endpoint/route.ts
export async function POST(req: Request) {
  const { data } = await req.json();

  // Access secrets safely on server
  const result = await fetch(process.env.EXTERNAL_API_URL, {
    headers: { Authorization: `Bearer ${process.env.API_SECRET}` },
    body: JSON.stringify(data)
  });

  return Response.json(await result.json());
}
```
