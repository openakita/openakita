# Sessions Reference

Stateful execution with warm workers.

## What Are Sessions?

Sessions keep workers warm between requests, enabling:

- **Faster execution** - No cold start on subsequent calls
- **Shared state** - Maintain context, loaded models, cached data
- **Cost efficiency** - Reuse initialized resources

## Creating a Session

```typescript
import { inference } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

// Start new session
const result = await client.run({
  app: 'my-app',
  input: { action: 'initialize' },
  session: 'new'
});

const sessionId = result.session_id;
console.log(`Session: ${sessionId}`);
```

## Using an Existing Session

```typescript
// Continue in same session
const result = await client.run({
  app: 'my-app',
  input: { action: 'process', data: '...' },
  session: sessionId
});
```

## Session Timeout

Set how long idle sessions stay alive (1-3600 seconds):

```typescript
// 5-minute timeout
const result = await client.run({
  app: 'my-app',
  input: { action: 'init' },
  session: 'new',
  session_timeout: 300
});
```

## Session Lifecycle

```
1. Create session (session: "new")
   ↓
2. Worker starts, initializes app
   ↓
3. Subsequent calls reuse worker (session: sessionId)
   ↓
4. Idle timeout reached or explicit close
   ↓
5. Worker terminates
```

## Use Cases

### Model Loading

Load a model once, use it multiple times:

```typescript
// Initial load (slow)
const result = await client.run({
  app: 'ml-inference',
  input: { action: 'load_model', model: 'large-model-v2' },
  session: 'new',
  session_timeout: 600
});
const sessionId = result.session_id;

// Fast inference calls
for (const item of dataBatch) {
  const result = await client.run({
    app: 'ml-inference',
    input: { action: 'predict', data: item },
    session: sessionId
  });
  console.log(result.output);
}
```

### Browser Automation

Keep browser open across multiple actions:

```typescript
// Start browser session
const result = await client.run({
  app: 'browser-automation',
  input: { action: 'start', url: 'https://example.com' },
  session: 'new',
  session_timeout: 300
});
const sessionId = result.session_id;

// Navigate
await client.run({
  app: 'browser-automation',
  input: { action: 'click', selector: '#login-btn' },
  session: sessionId
});

// Fill form
await client.run({
  app: 'browser-automation',
  input: { action: 'type', selector: '#username', text: 'user@example.com' },
  session: sessionId
});

// Take screenshot
const screenshot = await client.run({
  app: 'browser-automation',
  input: { action: 'screenshot' },
  session: sessionId
});
```

### Stateful Conversations

```typescript
// Initialize chat context
const result = await client.run({
  app: 'chat-with-memory',
  input: { action: 'init', system: 'You are a helpful assistant.' },
  session: 'new',
  session_timeout: 1800  // 30 minutes
});
const sessionId = result.session_id;

// Multi-turn conversation
const messages = [
  'What is quantum computing?',
  'Can you give me a simple example?',
  'How is it different from classical computing?'
];

for (const msg of messages) {
  const result = await client.run({
    app: 'chat-with-memory',
    input: { message: msg },
    session: sessionId
  });
  console.log(`Assistant: ${result.output.response}`);
}
```

### Data Processing Pipeline

```typescript
// Load data once
const result = await client.run({
  app: 'data-processor',
  input: { action: 'load', dataset: 'large_dataset.parquet' },
  session: 'new',
  session_timeout: 900
});
const sessionId = result.session_id;

// Run multiple analyses
const analyses = ['summary', 'correlations', 'outliers', 'trends'];

for (const analysis of analyses) {
  const result = await client.run({
    app: 'data-processor',
    input: { action: 'analyze', type: analysis },
    session: sessionId
  });
  console.log(`${analysis}:`, result.output);
}
```

## Session Management

### Session Recovery Pattern

```typescript
class SessionManager {
  private client: any;
  private app: string;
  private timeout: number;
  private sessionId: string | null = null;

  constructor(client: any, app: string, timeout = 300) {
    this.client = client;
    this.app = app;
    this.timeout = timeout;
  }

  private async ensureSession(): Promise<string> {
    if (!this.sessionId) {
      const result = await this.client.run({
        app: this.app,
        input: { action: 'init' },
        session: 'new',
        session_timeout: this.timeout
      });
      this.sessionId = result.session_id;
    }
    return this.sessionId;
  }

  async run(input: any) {
    try {
      return await this.client.run({
        app: this.app,
        input,
        session: await this.ensureSession()
      });
    } catch (e: any) {
      if (e.message?.toLowerCase().includes('session')) {
        // Session expired, create new one
        this.sessionId = null;
        return await this.client.run({
          app: this.app,
          input,
          session: await this.ensureSession()
        });
      }
      throw e;
    }
  }
}

// Usage
const manager = new SessionManager(client, 'my-app', 600);
const result = await manager.run({ action: 'process', data: '...' });
```

## React Hook for Sessions

```typescript
import { useState, useCallback, useRef } from 'react';
import { inference } from '@inferencesh/sdk';

function useSession(app: string, timeout = 300) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const clientRef = useRef(inference({ proxyUrl: '/api/inference/proxy' }));

  const run = useCallback(async (input: any) => {
    setLoading(true);

    try {
      const isNew = !sessionId;
      const result = await clientRef.current.run({
        app,
        input,
        session: isNew ? 'new' : sessionId,
        ...(isNew && { session_timeout: timeout })
      });

      if (isNew) {
        setSessionId(result.session_id);
      }

      return result;
    } catch (e: any) {
      if (e.message?.toLowerCase().includes('session')) {
        // Session expired
        setSessionId(null);
      }
      throw e;
    } finally {
      setLoading(false);
    }
  }, [app, sessionId, timeout]);

  const reset = useCallback(() => {
    setSessionId(null);
  }, []);

  return { sessionId, loading, run, reset };
}

// Usage
function BrowserAutomation() {
  const { sessionId, loading, run, reset } = useSession('browser-automation');

  return (
    <div>
      <div>Session: {sessionId || 'None'}</div>
      <button onClick={() => run({ action: 'start', url: '...' })} disabled={loading}>
        Start Browser
      </button>
      <button onClick={() => run({ action: 'screenshot' })} disabled={!sessionId || loading}>
        Screenshot
      </button>
      <button onClick={reset}>End Session</button>
    </div>
  );
}
```

## Express Session API

```typescript
import express from 'express';
import { inference } from '@inferencesh/sdk';

const app = express();
const client = inference({ apiKey: process.env.INFERENCE_API_KEY });

// Store sessions per user
const userSessions: Map<string, string> = new Map();

app.post('/api/browser/start', async (req, res) => {
  const userId = req.user.id;

  const result = await client.run({
    app: 'browser-automation',
    input: { action: 'start', url: req.body.url },
    session: 'new',
    session_timeout: 300
  });

  userSessions.set(userId, result.session_id);
  res.json({ sessionId: result.session_id });
});

app.post('/api/browser/action', async (req, res) => {
  const userId = req.user.id;
  const sessionId = userSessions.get(userId);

  if (!sessionId) {
    return res.status(400).json({ error: 'No active session' });
  }

  try {
    const result = await client.run({
      app: 'browser-automation',
      input: req.body,
      session: sessionId
    });
    res.json(result.output);
  } catch (e: any) {
    if (e.message?.includes('session')) {
      userSessions.delete(userId);
      res.status(400).json({ error: 'Session expired' });
    } else {
      throw e;
    }
  }
});

app.post('/api/browser/end', (req, res) => {
  const userId = req.user.id;
  userSessions.delete(userId);
  res.json({ ok: true });
});
```

## Best Practices

1. **Set appropriate timeouts** - Balance between keeping workers warm and resource usage
2. **Handle session expiry** - Always catch and handle session not found errors
3. **Clean up when done** - Delete session references when user is finished
4. **Don't over-parallelize** - Session requests go to the same worker sequentially
5. **Monitor costs** - Long-running sessions incur ongoing charges
6. **Store session IDs securely** - Don't expose session IDs to untrusted clients
