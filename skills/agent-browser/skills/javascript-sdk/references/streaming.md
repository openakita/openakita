# Streaming Reference

Real-time progress updates and Server-Sent Events (SSE) handling.

## Task Status Flow

```
RECEIVED (1) → QUEUED (2) → SCHEDULED (3) → PREPARING (4)
→ SERVING (5) → SETTING_UP (6) → RUNNING (7) → UPLOADING (8)
→ COMPLETED (10), FAILED (11), or CANCELLED (12)
```

## Basic Streaming

```typescript
import { inference } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

const stream = await client.run({
  app: 'google/veo-3-1-fast',
  input: { prompt: 'A sunset timelapse' }
}, { stream: true });

for await (const update of stream) {
  console.log(`Status: ${update.status}`);
}
```

## Handling Different Update Types

```typescript
const stream = await client.run(config, { stream: true });

for await (const update of stream) {
  const { status } = update;

  // Task state changes
  if (status === 'queued') {
    console.log('Task queued, waiting for worker...');
  } else if (status === 'running') {
    console.log('Task is running...');
  } else if (status === 'completed') {
    console.log('Done!');
    console.log('Output:', update.output);
  } else if (status === 'failed') {
    console.log('Error:', update.error);
  }

  // Progress logs
  if (update.logs?.length) {
    for (const log of update.logs) {
      console.log(`  Log: ${log}`);
    }
  }

  // Partial outputs
  if (update.partial_output) {
    console.log(`  Partial: ${update.partial_output}`);
  }
}
```

## Progress Tracking with UI

### Node.js CLI Progress Bar

```typescript
import { inference } from '@inferencesh/sdk';

function progressBar(current: number, total: number, width = 50) {
  const filled = Math.round(width * current / total);
  const bar = '█'.repeat(filled) + '░'.repeat(width - filled);
  const percent = (current / total * 100).toFixed(1);
  process.stdout.write(`\r[${bar}] ${percent}%`);
}

const stream = await client.run(config, { stream: true });

for await (const update of stream) {
  if (update.progress) {
    progressBar(update.progress.current, update.progress.total);
  }

  if (update.status === 'completed') {
    console.log('\n✓ Complete!');
  }
}
```

### React Progress Component

```typescript
import { useState, useEffect } from 'react';
import { inference } from '@inferencesh/sdk';

function ProgressDisplay({ config }: { config: any }) {
  const [status, setStatus] = useState('idle');
  const [progress, setProgress] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);

  useEffect(() => {
    const client = inference({ proxyUrl: '/api/inference/proxy' });

    async function run() {
      const stream = await client.run(config, { stream: true });

      for await (const update of stream) {
        setStatus(update.status);

        if (update.progress) {
          setProgress(update.progress.current / update.progress.total * 100);
        }

        if (update.logs) {
          setLogs(prev => [...prev, ...update.logs]);
        }
      }
    }

    run();
  }, [config]);

  return (
    <div>
      <div>Status: {status}</div>
      <progress value={progress} max={100} />
      <ul>
        {logs.map((log, i) => <li key={i}>{log}</li>)}
      </ul>
    </div>
  );
}
```

## Streaming with Timeout

```typescript
async function streamWithTimeout(config: any, timeoutMs: number) {
  const client = inference({ apiKey: 'inf_...' });
  const start = Date.now();

  const stream = await client.run(config, { stream: true });

  for await (const update of stream) {
    if (Date.now() - start > timeoutMs) {
      console.log('Timeout reached');
      break;
    }

    console.log(`Status: ${update.status}`);

    if (['completed', 'failed'].includes(update.status)) {
      return update;
    }
  }
}

const result = await streamWithTimeout(config, 60000); // 1 minute
```

## Agent Streaming

```typescript
const agent = client.agent('my-org/assistant@latest');

const response = await agent.sendMessage('Explain quantum entanglement', {
  onMessage: (msg) => {
    if (msg.content) {
      // Stream text as it arrives
      process.stdout.write(msg.content);
    }

    if (msg.type === 'thinking') {
      console.log(`\n[Thinking: ${msg.content}]`);
    }
  },
  onToolCall: async (call) => {
    console.log(`\n[Calling tool: ${call.name}]`);
    const result = await executeTool(call.name, call.args);
    agent.submitToolResult(call.id, result);
  }
});
```

## Multiple Streams in Parallel

```typescript
async function parallelStreams() {
  const client = inference({ apiKey: 'inf_...' });

  const configs = [
    { app: 'infsh/flux-schnell', input: { prompt: 'A mountain' } },
    { app: 'infsh/flux-schnell', input: { prompt: 'An ocean' } },
    { app: 'infsh/flux-schnell', input: { prompt: 'A forest' } }
  ];

  async function streamOne(config: any, index: number) {
    const stream = await client.run(config, { stream: true });

    for await (const update of stream) {
      console.log(`[${index}] ${update.status}`);

      if (update.status === 'completed') {
        return update.output;
      }
    }
  }

  const results = await Promise.all(
    configs.map((c, i) => streamOne(c, i))
  );

  return results;
}
```

## Cancelling a Stream

```typescript
async function cancellableStream(config: any) {
  const client = inference({ apiKey: 'inf_...' });
  const controller = new AbortController();

  // Cancel after 10 seconds
  setTimeout(() => controller.abort(), 10000);

  try {
    const stream = await client.run(config, {
      stream: true,
      signal: controller.signal
    });

    for await (const update of stream) {
      console.log(update.status);
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      console.log('Stream cancelled');
    } else {
      throw e;
    }
  }
}
```

## Collecting All Logs

```typescript
async function collectLogs(config: any) {
  const client = inference({ apiKey: 'inf_...' });
  const allLogs: string[] = [];

  const stream = await client.run(config, { stream: true });

  for await (const update of stream) {
    if (update.logs) {
      allLogs.push(...update.logs);
    }

    if (update.status === 'completed') {
      console.log('Final logs:');
      allLogs.forEach(log => console.log(`  ${log}`));
      return update.output;
    }
  }
}
```

## Custom Stream Processor Class

```typescript
class StreamProcessor {
  logs: string[] = [];
  startTime?: number;
  endTime?: number;

  process(update: any): boolean {
    if (!this.startTime) {
      this.startTime = Date.now();
    }

    if (update.logs) {
      this.logs.push(...update.logs);
    }

    if (['completed', 'failed'].includes(update.status)) {
      this.endTime = Date.now();
      return true; // Done
    }

    return false; // Continue
  }

  get duration(): number | null {
    if (this.startTime && this.endTime) {
      return this.endTime - this.startTime;
    }
    return null;
  }
}

// Usage
const processor = new StreamProcessor();
const stream = await client.run(config, { stream: true });

for await (const update of stream) {
  if (processor.process(update)) {
    break;
  }
}

console.log(`Duration: ${processor.duration}ms`);
console.log(`Logs: ${processor.logs.length}`);
```

## Server-Sent Events in Browser

```typescript
// For custom SSE handling in browser
async function browserSSE(taskId: string) {
  const eventSource = new EventSource(
    `/api/inference/stream?taskId=${taskId}`
  );

  eventSource.onmessage = (event) => {
    const update = JSON.parse(event.data);
    console.log('Update:', update);

    if (['completed', 'failed'].includes(update.status)) {
      eventSource.close();
    }
  };

  eventSource.onerror = (error) => {
    console.error('SSE error:', error);
    eventSource.close();
  };
}
```

## React Hook for Streaming

```typescript
import { useState, useCallback, useRef } from 'react';
import { inference } from '@inferencesh/sdk';

interface StreamState {
  status: string;
  output: any;
  logs: string[];
  error: string | null;
}

function useStream() {
  const [state, setState] = useState<StreamState>({
    status: 'idle',
    output: null,
    logs: [],
    error: null
  });
  const controllerRef = useRef<AbortController | null>(null);

  const run = useCallback(async (config: any) => {
    const client = inference({ proxyUrl: '/api/inference/proxy' });
    controllerRef.current = new AbortController();

    setState({ status: 'starting', output: null, logs: [], error: null });

    try {
      const stream = await client.run(config, {
        stream: true,
        signal: controllerRef.current.signal
      });

      for await (const update of stream) {
        setState(prev => ({
          ...prev,
          status: update.status,
          logs: update.logs ? [...prev.logs, ...update.logs] : prev.logs,
          output: update.output || prev.output
        }));
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setState(prev => ({ ...prev, status: 'error', error: e.message }));
      }
    }
  }, []);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
  }, []);

  return { ...state, run, cancel };
}

// Usage in component
function Generator() {
  const { status, output, logs, run, cancel } = useStream();

  return (
    <div>
      <button onClick={() => run({ app: 'my-app', input: {...} })}>
        Start
      </button>
      <button onClick={cancel}>Cancel</button>
      <div>Status: {status}</div>
      {output && <img src={output.url} />}
    </div>
  );
}
```
