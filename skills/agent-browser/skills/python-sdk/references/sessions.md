# Sessions Reference

Stateful execution with warm workers.

## What Are Sessions?

Sessions keep workers warm between requests, enabling:

- **Faster execution** - No cold start on subsequent calls
- **Shared state** - Maintain context, loaded models, cached data
- **Cost efficiency** - Reuse initialized resources

## Creating a Session

```python
from inferencesh import inference

client = inference(api_key="inf_...")

# Start new session
result = client.run({
    "app": "my-app",
    "input": {"action": "initialize"},
    "session": "new"
})

session_id = result["session_id"]
print(f"Session: {session_id}")
```

## Using an Existing Session

```python
# Continue in same session
result = client.run({
    "app": "my-app",
    "input": {"action": "process", "data": "..."},
    "session": session_id
})
```

## Session Timeout

Set how long idle sessions stay alive (1-3600 seconds):

```python
# 5-minute timeout
result = client.run({
    "app": "my-app",
    "input": {"action": "init"},
    "session": "new",
    "session_timeout": 300
})
```

## Session Lifecycle

```
1. Create session (session: "new")
   ↓
2. Worker starts, initializes app
   ↓
3. Subsequent calls reuse worker (session: session_id)
   ↓
4. Idle timeout reached or explicit close
   ↓
5. Worker terminates
```

## Use Cases

### Model Loading

Load a model once, use it multiple times:

```python
# Initial load (slow)
result = client.run({
    "app": "ml-inference",
    "input": {"action": "load_model", "model": "large-model-v2"},
    "session": "new",
    "session_timeout": 600
})
session_id = result["session_id"]

# Fast inference calls
for item in data_batch:
    result = client.run({
        "app": "ml-inference",
        "input": {"action": "predict", "data": item},
        "session": session_id
    })
    print(result["output"])
```

### Browser Automation

Keep browser open across multiple actions:

```python
# Start browser session
result = client.run({
    "app": "browser-automation",
    "input": {"action": "start", "url": "https://example.com"},
    "session": "new",
    "session_timeout": 300
})
session_id = result["session_id"]

# Navigate
client.run({
    "app": "browser-automation",
    "input": {"action": "click", "selector": "#login-btn"},
    "session": session_id
})

# Fill form
client.run({
    "app": "browser-automation",
    "input": {"action": "type", "selector": "#username", "text": "user@example.com"},
    "session": session_id
})

# Take screenshot
result = client.run({
    "app": "browser-automation",
    "input": {"action": "screenshot"},
    "session": session_id
})
```

### Stateful Conversations

```python
# Initialize chat context
result = client.run({
    "app": "chat-with-memory",
    "input": {"action": "init", "system": "You are a helpful assistant."},
    "session": "new",
    "session_timeout": 1800  # 30 minutes
})
session_id = result["session_id"]

# Multi-turn conversation
messages = [
    "What is quantum computing?",
    "Can you give me a simple example?",
    "How is it different from classical computing?"
]

for msg in messages:
    result = client.run({
        "app": "chat-with-memory",
        "input": {"message": msg},
        "session": session_id
    })
    print(f"Assistant: {result['output']['response']}")
```

### Data Processing Pipeline

```python
# Load data once
result = client.run({
    "app": "data-processor",
    "input": {"action": "load", "dataset": "large_dataset.parquet"},
    "session": "new",
    "session_timeout": 900
})
session_id = result["session_id"]

# Run multiple analyses
analyses = ["summary", "correlations", "outliers", "trends"]

for analysis in analyses:
    result = client.run({
        "app": "data-processor",
        "input": {"action": "analyze", "type": analysis},
        "session": session_id
    })
    print(f"{analysis}: {result['output']}")
```

## Session Management

### Check Session Status

```python
# Sessions are implicitly active when used
# If session expired, you'll get an error
try:
    result = client.run({
        "app": "my-app",
        "input": {"action": "check"},
        "session": session_id
    })
except Exception as e:
    if "session not found" in str(e).lower():
        print("Session expired, creating new one")
        # Create new session
```

### Explicit Session Close

```python
# Close session to free resources
client.run({
    "app": "my-app",
    "input": {"action": "cleanup"},
    "session": session_id
})
# Session will terminate after this call
```

### Session Recovery Pattern

```python
class SessionManager:
    def __init__(self, client, app, timeout=300):
        self.client = client
        self.app = app
        self.timeout = timeout
        self.session_id = None

    def ensure_session(self):
        if self.session_id is None:
            result = self.client.run({
                "app": self.app,
                "input": {"action": "init"},
                "session": "new",
                "session_timeout": self.timeout
            })
            self.session_id = result["session_id"]
        return self.session_id

    def run(self, input_data):
        try:
            return self.client.run({
                "app": self.app,
                "input": input_data,
                "session": self.ensure_session()
            })
        except Exception as e:
            if "session" in str(e).lower():
                # Session expired, create new one
                self.session_id = None
                return self.client.run({
                    "app": self.app,
                    "input": input_data,
                    "session": self.ensure_session()
                })
            raise

# Usage
manager = SessionManager(client, "my-app", timeout=600)
result = manager.run({"action": "process", "data": "..."})
```

## Async Sessions

```python
from inferencesh import async_inference
import asyncio

async def session_workflow():
    client = async_inference(api_key="inf_...")

    # Create session
    result = await client.run({
        "app": "my-app",
        "input": {"action": "init"},
        "session": "new",
        "session_timeout": 300
    })
    session_id = result["session_id"]

    # Run operations
    tasks = [
        client.run({
            "app": "my-app",
            "input": {"action": "process", "id": i},
            "session": session_id
        })
        for i in range(10)
    ]

    # Note: These run sequentially on the same worker
    results = []
    for task in tasks:
        results.append(await task)

    return results

asyncio.run(session_workflow())
```

## Best Practices

1. **Set appropriate timeouts** - Balance between keeping workers warm and resource usage
2. **Handle session expiry** - Always catch and handle session not found errors
3. **Clean up when done** - Close sessions explicitly if you know you're finished
4. **Don't over-parallelize** - Session requests go to the same worker sequentially
5. **Monitor costs** - Long-running sessions incur ongoing charges
