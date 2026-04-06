# React Integration Reference

Build AI-powered React applications with the inference.sh SDK.

## Setup

```bash
npm install @inferencesh/sdk
```

Configure proxy (keep API keys on server):

```typescript
// app/api/inference/proxy/route.ts (Next.js)
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export const POST = route.POST;
```

## Basic Client Setup

```typescript
import { inference } from '@inferencesh/sdk';

// Create client with proxy (no API key in browser)
const client = inference({ proxyUrl: '/api/inference/proxy' });
```

## useInference Hook

Custom hook for running AI apps:

```typescript
import { useState, useCallback } from 'react';
import { inference } from '@inferencesh/sdk';

interface UseInferenceState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

function useInference<T = any>() {
  const [state, setState] = useState<UseInferenceState<T>>({
    data: null,
    loading: false,
    error: null
  });

  const client = inference({ proxyUrl: '/api/inference/proxy' });

  const run = useCallback(async (config: any) => {
    setState({ data: null, loading: true, error: null });

    try {
      const result = await client.run(config);
      setState({ data: result.output as T, loading: false, error: null });
      return result.output as T;
    } catch (e: any) {
      setState({ data: null, loading: false, error: e.message });
      throw e;
    }
  }, []);

  return { ...state, run };
}

// Usage
function ImageGenerator() {
  const { data, loading, error, run } = useInference<{ image: string }>();

  const generate = () => run({
    app: 'infsh/flux-schnell',
    input: { prompt: 'A sunset' }
  });

  return (
    <div>
      <button onClick={generate} disabled={loading}>
        {loading ? 'Generating...' : 'Generate'}
      </button>
      {error && <div className="error">{error}</div>}
      {data && <img src={data.image} alt="Generated" />}
    </div>
  );
}
```

## useChat Hook

Conversational AI with streaming:

```typescript
import { useState, useCallback, useRef } from 'react';
import { inference } from '@inferencesh/sdk';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

function useChat(agentRef: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);

  const clientRef = useRef(inference({ proxyUrl: '/api/inference/proxy' }));
  const agentRef_ = useRef(clientRef.current.agent(agentRef));

  const sendMessage = useCallback(async (content: string) => {
    // Add user message
    setMessages(prev => [...prev, { role: 'user', content }]);
    setLoading(true);
    setStreaming(true);

    // Placeholder for assistant response
    let assistantContent = '';
    setMessages(prev => [...prev, { role: 'assistant', content: '' }]);

    try {
      await agentRef_.current.sendMessage(content, {
        onMessage: (msg) => {
          if (msg.content) {
            assistantContent += msg.content;
            setMessages(prev => [
              ...prev.slice(0, -1),
              { role: 'assistant', content: assistantContent }
            ]);
          }
        }
      });
    } finally {
      setLoading(false);
      setStreaming(false);
    }
  }, []);

  const reset = useCallback(() => {
    agentRef_.current.reset();
    setMessages([]);
  }, []);

  return { messages, loading, streaming, sendMessage, reset };
}

// Usage
function ChatComponent() {
  const { messages, loading, sendMessage, reset } = useChat('my-org/assistant@latest');
  const [input, setInput] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim()) {
      sendMessage(input);
      setInput('');
    }
  };

  return (
    <div className="chat">
      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.content}
          </div>
        ))}
      </div>

      <form onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          disabled={loading}
        />
        <button type="submit" disabled={loading}>Send</button>
      </form>

      <button onClick={reset}>New Chat</button>
    </div>
  );
}
```

## useStream Hook

Stream task progress:

```typescript
import { useState, useCallback, useRef } from 'react';
import { inference } from '@inferencesh/sdk';

interface StreamState {
  status: string;
  progress: number;
  logs: string[];
  output: any;
  error: string | null;
}

function useStream() {
  const [state, setState] = useState<StreamState>({
    status: 'idle',
    progress: 0,
    logs: [],
    output: null,
    error: null
  });

  const controllerRef = useRef<AbortController | null>(null);
  const client = inference({ proxyUrl: '/api/inference/proxy' });

  const run = useCallback(async (config: any) => {
    controllerRef.current = new AbortController();

    setState({
      status: 'starting',
      progress: 0,
      logs: [],
      output: null,
      error: null
    });

    try {
      const stream = await client.run(config, {
        stream: true,
        signal: controllerRef.current.signal
      });

      for await (const update of stream) {
        setState(prev => ({
          ...prev,
          status: update.status,
          progress: update.progress
            ? (update.progress.current / update.progress.total) * 100
            : prev.progress,
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
    setState(prev => ({ ...prev, status: 'cancelled' }));
  }, []);

  return { ...state, run, cancel };
}

// Usage
function VideoGenerator() {
  const { status, progress, logs, output, run, cancel } = useStream();

  return (
    <div>
      <button onClick={() => run({
        app: 'google/veo-3-1-fast',
        input: { prompt: 'Ocean waves' }
      })}>
        Generate Video
      </button>
      <button onClick={cancel}>Cancel</button>

      <div>Status: {status}</div>
      <progress value={progress} max={100} />

      <div className="logs">
        {logs.map((log, i) => <div key={i}>{log}</div>)}
      </div>

      {output && <video src={output.video} controls />}
    </div>
  );
}
```

## File Upload Component

```typescript
import { useState, useCallback } from 'react';
import { inference } from '@inferencesh/sdk';

function FileUpload({ onUpload }: { onUpload: (uri: string) => void }) {
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);

  const client = inference({ proxyUrl: '/api/inference/proxy' });

  const handleFile = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Show preview
    const reader = new FileReader();
    reader.onload = () => setPreview(reader.result as string);
    reader.readAsDataURL(file);

    // Upload
    setUploading(true);
    try {
      const uploaded = await client.uploadFile(file);
      onUpload(uploaded.uri);
    } finally {
      setUploading(false);
    }
  }, [onUpload]);

  return (
    <div>
      <input type="file" onChange={handleFile} disabled={uploading} />
      {uploading && <span>Uploading...</span>}
      {preview && <img src={preview} alt="Preview" style={{ maxWidth: 200 }} />}
    </div>
  );
}
```

## Image Analysis Component

```typescript
function ImageAnalyzer() {
  const [imageUri, setImageUri] = useState<string | null>(null);
  const { messages, sendMessage, loading } = useChat('my-org/vision-assistant@latest');

  const handleUpload = (uri: string) => {
    setImageUri(uri);
  };

  const analyze = () => {
    if (imageUri) {
      sendMessage(`Analyze this image: ${imageUri}`);
    }
  };

  return (
    <div>
      <FileUpload onUpload={handleUpload} />

      <button onClick={analyze} disabled={!imageUri || loading}>
        Analyze Image
      </button>

      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={msg.role}>{msg.content}</div>
        ))}
      </div>
    </div>
  );
}
```

## Tool Handling Component

```typescript
import { useState, useCallback } from 'react';
import { inference, tool, string } from '@inferencesh/sdk';

function AgentWithTools() {
  const [response, setResponse] = useState('');
  const [pendingTool, setPendingTool] = useState<any>(null);

  const client = inference({ proxyUrl: '/api/inference/proxy' });

  const confirmTool = tool('confirm_action')
    .describe('Confirm a destructive action')
    .param('action', string('Action to confirm'))
    .requireApproval()
    .build();

  const agent = client.agent({
    core_app: { ref: 'infsh/claude-sonnet-4@latest' },
    tools: [confirmTool]
  });

  const handleMessage = useCallback(async (message: string) => {
    setResponse('');

    await agent.sendMessage(message, {
      onMessage: (msg) => {
        if (msg.content) {
          setResponse(prev => prev + msg.content);
        }
      },
      onToolCall: (call) => {
        if (call.requiresApproval) {
          setPendingTool(call);
        }
      }
    });
  }, []);

  const handleApprove = (approved: boolean) => {
    if (pendingTool) {
      if (approved) {
        agent.submitToolResult(pendingTool.id, { confirmed: true });
      } else {
        agent.submitToolResult(pendingTool.id, { error: 'Denied by user' });
      }
      setPendingTool(null);
    }
  };

  return (
    <div>
      <button onClick={() => handleMessage('Delete all temp files')}>
        Run Agent
      </button>

      <div>{response}</div>

      {pendingTool && (
        <div className="approval-dialog">
          <p>Agent wants to: {pendingTool.args.action}</p>
          <button onClick={() => handleApprove(true)}>Approve</button>
          <button onClick={() => handleApprove(false)}>Deny</button>
        </div>
      )}
    </div>
  );
}
```

## Context Provider

Share client across components:

```typescript
import { createContext, useContext, useMemo, ReactNode } from 'react';
import { inference, type InferenceClient } from '@inferencesh/sdk';

const InferenceContext = createContext<InferenceClient | null>(null);

export function InferenceProvider({ children }: { children: ReactNode }) {
  const client = useMemo(() =>
    inference({ proxyUrl: '/api/inference/proxy' }),
    []
  );

  return (
    <InferenceContext.Provider value={client}>
      {children}
    </InferenceContext.Provider>
  );
}

export function useInferenceClient() {
  const client = useContext(InferenceContext);
  if (!client) {
    throw new Error('useInferenceClient must be used within InferenceProvider');
  }
  return client;
}

// Usage in app
function App() {
  return (
    <InferenceProvider>
      <ChatComponent />
      <ImageGenerator />
    </InferenceProvider>
  );
}

// Usage in component
function MyComponent() {
  const client = useInferenceClient();
  // Use client...
}
```

## Suspense Integration

```typescript
import { Suspense } from 'react';
import { use } from 'react';
import { inference } from '@inferencesh/sdk';

const client = inference({ proxyUrl: '/api/inference/proxy' });

// Create a promise for the resource
function createImageResource(prompt: string) {
  const promise = client.run({
    app: 'infsh/flux-schnell',
    input: { prompt }
  }).then(r => r.output.image);

  return { read: () => use(promise) };
}

function GeneratedImage({ resource }: { resource: ReturnType<typeof createImageResource> }) {
  const imageUrl = resource.read();
  return <img src={imageUrl} alt="Generated" />;
}

function App() {
  const [resource, setResource] = useState<any>(null);

  const generate = () => {
    setResource(createImageResource('A sunset'));
  };

  return (
    <div>
      <button onClick={generate}>Generate</button>
      {resource && (
        <Suspense fallback={<div>Generating...</div>}>
          <GeneratedImage resource={resource} />
        </Suspense>
      )}
    </div>
  );
}
```

## Error Boundary

```typescript
import { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

class InferenceErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <div className="error">
          <h2>Something went wrong</h2>
          <p>{this.state.error?.message}</p>
          <button onClick={() => this.setState({ hasError: false, error: null })}>
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

// Usage
function App() {
  return (
    <InferenceErrorBoundary>
      <AIFeatures />
    </InferenceErrorBoundary>
  );
}
```
