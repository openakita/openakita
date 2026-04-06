# Server Proxy Setup

Secure API key handling for frontend applications.

## Why Use a Proxy?

Never expose API keys in browser code. Use a server proxy to:

- Keep API keys secure on the server
- Add authentication/authorization
- Rate limit requests
- Log usage and analytics

```
Browser (SDK) → Your Backend (Proxy) → api.inference.sh
  (no key)     (injects API key)      (authenticated)
```

## Client Configuration

```typescript
import { inference } from '@inferencesh/sdk';

// Frontend code - no API key!
const client = inference({
  proxyUrl: '/api/inference/proxy'
});

// Use normally
const result = await client.run({
  app: 'infsh/flux-schnell',
  input: { prompt: 'A sunset' }
});
```

## Next.js (App Router)

```typescript
// app/api/inference/proxy/route.ts
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export const POST = route.POST;
```

### With Custom Authentication

```typescript
// app/api/inference/proxy/route.ts
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';
import { getServerSession } from 'next-auth';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export async function POST(req: Request) {
  // Check auth first
  const session = await getServerSession();
  if (!session) {
    return Response.json({ error: 'Unauthorized' }, { status: 401 });
  }

  // Check rate limits, log usage, etc.

  return route.POST(req);
}
```

## Next.js (Pages Router)

```typescript
// pages/api/inference/proxy.ts
import { createApiHandler } from '@inferencesh/sdk/proxy/nextjs-pages';

export default createApiHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});
```

## Express

```typescript
import express from 'express';
import { createProxyMiddleware } from '@inferencesh/sdk/proxy/express';

const app = express();

// Basic setup
app.use('/api/inference/proxy', createProxyMiddleware({
  apiKey: process.env.INFERENCE_API_KEY!
}));

// With auth middleware
app.use('/api/inference/proxy',
  requireAuth,
  createProxyMiddleware({
    apiKey: process.env.INFERENCE_API_KEY!
  })
);

app.listen(3000);
```

## Hono

```typescript
import { Hono } from 'hono';
import { createMiddleware } from '@inferencesh/sdk/proxy/hono';

const app = new Hono();

app.post('/api/inference/proxy', createMiddleware({
  apiKey: process.env.INFERENCE_API_KEY!
}));

export default app;
```

### Cloudflare Workers

```typescript
import { Hono } from 'hono';
import { createMiddleware } from '@inferencesh/sdk/proxy/hono';

type Bindings = {
  INFERENCE_API_KEY: string;
};

const app = new Hono<{ Bindings: Bindings }>();

app.post('/api/inference/proxy', (c) => {
  const middleware = createMiddleware({
    apiKey: c.env.INFERENCE_API_KEY
  });
  return middleware(c);
});

export default app;
```

## Remix

```typescript
// app/routes/api.inference.proxy.tsx
import type { ActionFunctionArgs } from '@remix-run/node';
import { createActionHandler } from '@inferencesh/sdk/proxy/remix';

const handler = createActionHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export const action = async ({ request }: ActionFunctionArgs) => {
  return handler(request);
};
```

## SvelteKit

```typescript
// src/routes/api/inference/proxy/+server.ts
import { createRequestHandler } from '@inferencesh/sdk/proxy/sveltekit';
import { INFERENCE_API_KEY } from '$env/static/private';

const handler = createRequestHandler({
  apiKey: INFERENCE_API_KEY
});

export const POST = handler;
```

## Dynamic API Key Resolution

Load API keys from secrets managers:

```typescript
// Next.js example
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';
import { getSecret } from './vault';

const route = createRouteHandler({
  resolveApiKey: async () => {
    return await getSecret('INFERENCE_API_KEY');
  }
});

export const POST = route.POST;
```

## Per-User API Keys

Use different keys per user/organization:

```typescript
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';
import { getServerSession } from 'next-auth';
import { db } from './db';

const route = createRouteHandler({
  resolveApiKey: async (req) => {
    const session = await getServerSession();
    if (!session?.user) {
      throw new Error('Unauthorized');
    }

    // Get user's API key from database
    const user = await db.user.findUnique({
      where: { id: session.user.id },
      select: { inferenceApiKey: true }
    });

    return user?.inferenceApiKey || process.env.DEFAULT_INFERENCE_API_KEY!;
  }
});

export const POST = route.POST;
```

## Rate Limiting

```typescript
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';
import { Ratelimit } from '@upstash/ratelimit';
import { Redis } from '@upstash/redis';

const ratelimit = new Ratelimit({
  redis: Redis.fromEnv(),
  limiter: Ratelimit.slidingWindow(10, '1 m'), // 10 requests per minute
});

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export async function POST(req: Request) {
  // Get user identifier
  const ip = req.headers.get('x-forwarded-for') || 'anonymous';

  // Check rate limit
  const { success, limit, reset, remaining } = await ratelimit.limit(ip);

  if (!success) {
    return Response.json(
      { error: 'Rate limit exceeded' },
      {
        status: 429,
        headers: {
          'X-RateLimit-Limit': limit.toString(),
          'X-RateLimit-Remaining': remaining.toString(),
          'X-RateLimit-Reset': reset.toString()
        }
      }
    );
  }

  return route.POST(req);
}
```

## Usage Logging

```typescript
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';
import { getServerSession } from 'next-auth';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export async function POST(req: Request) {
  const session = await getServerSession();
  const body = await req.clone().json();

  // Log before
  console.log({
    type: 'inference_request',
    userId: session?.user?.id,
    app: body.app,
    timestamp: new Date().toISOString()
  });

  const response = await route.POST(req);

  // Log after
  console.log({
    type: 'inference_response',
    userId: session?.user?.id,
    status: response.status,
    timestamp: new Date().toISOString()
  });

  return response;
}
```

## Error Handling

```typescript
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export async function POST(req: Request) {
  try {
    return await route.POST(req);
  } catch (error) {
    console.error('Proxy error:', error);

    // Don't expose internal errors to client
    return Response.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}
```

## Vercel Deployment

```typescript
// vercel.json
{
  "functions": {
    "app/api/inference/proxy/route.ts": {
      "maxDuration": 60
    }
  }
}
```

```typescript
// app/api/inference/proxy/route.ts
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export const POST = route.POST;

// Enable streaming for long-running requests
export const runtime = 'edge';
```

## CORS Configuration

If your frontend and backend are on different domains:

```typescript
import { createRouteHandler } from '@inferencesh/sdk/proxy/nextjs';

const route = createRouteHandler({
  apiKey: process.env.INFERENCE_API_KEY!
});

export async function POST(req: Request) {
  const response = await route.POST(req);

  // Add CORS headers
  const headers = new Headers(response.headers);
  headers.set('Access-Control-Allow-Origin', 'https://your-frontend.com');
  headers.set('Access-Control-Allow-Methods', 'POST');
  headers.set('Access-Control-Allow-Headers', 'Content-Type');

  return new Response(response.body, {
    status: response.status,
    headers
  });
}

export async function OPTIONS() {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': 'https://your-frontend.com',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    }
  });
}
```

## Testing Locally

```typescript
// Use environment variable for flexibility
const client = inference({
  // Use proxy in production, direct API in development
  ...(process.env.NODE_ENV === 'production'
    ? { proxyUrl: '/api/inference/proxy' }
    : { apiKey: process.env.NEXT_PUBLIC_INFERENCE_API_KEY })
});
```
