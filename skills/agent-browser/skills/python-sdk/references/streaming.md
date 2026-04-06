# Streaming Reference

Real-time progress updates and Server-Sent Events (SSE) handling.

## Task Status Flow

```
RECEIVED (1) → QUEUED (2) → SCHEDULED (3) → PREPARING (4)
→ SERVING (5) → SETTING_UP (6) → RUNNING (7) → UPLOADING (8)
→ COMPLETED (10), FAILED (11), or CANCELLED (12)
```

## Basic Streaming

```python
from inferencesh import inference

client = inference(api_key="inf_...")

for update in client.run({
    "app": "google/veo-3-1-fast",
    "input": {"prompt": "A sunset timelapse"}
}, stream=True):
    print(f"Status: {update['status']}")
```

## Handling Different Update Types

```python
for update in client.run(config, stream=True):
    status = update.get("status")

    # Task state changes
    if status == "queued":
        print("Task queued, waiting for worker...")
    elif status == "running":
        print("Task is running...")
    elif status == "completed":
        print("Done!")
        print(f"Output: {update.get('output')}")
    elif status == "failed":
        print(f"Error: {update.get('error')}")

    # Progress logs
    if update.get("logs"):
        for log in update["logs"]:
            print(f"  Log: {log}")

    # Partial outputs
    if update.get("partial_output"):
        print(f"  Partial: {update['partial_output']}")
```

## Progress Tracking with UI

```python
import sys

def progress_bar(current, total, width=50):
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    percent = current / total * 100
    sys.stdout.write(f"\r[{bar}] {percent:.1f}%")
    sys.stdout.flush()

for update in client.run(config, stream=True):
    if update.get("progress"):
        progress_bar(update["progress"]["current"], update["progress"]["total"])

    if update.get("status") == "completed":
        print("\n✓ Complete!")
```

## Streaming with Timeout

```python
import time

start = time.time()
timeout = 300  # 5 minutes

for update in client.run(config, stream=True):
    if time.time() - start > timeout:
        print("Timeout reached")
        break

    print(f"Status: {update['status']}")

    if update.get("status") in ["completed", "failed"]:
        break
```

## Async Streaming

```python
from inferencesh import async_inference
import asyncio

async def stream_task():
    client = async_inference(api_key="inf_...")

    async for update in client.run({
        "app": "google/veo-3-1-fast",
        "input": {"prompt": "Ocean waves"}
    }, stream=True):
        print(f"Status: {update['status']}")

        if update.get("status") == "completed":
            return update.get("output")

result = asyncio.run(stream_task())
```

## Agent Streaming

```python
agent = client.agent("my-org/assistant@latest")

def on_message(msg):
    if msg.get("content"):
        # Stream text as it arrives
        print(msg["content"], end="", flush=True)

    if msg.get("type") == "thinking":
        print(f"\n[Thinking: {msg.get('content')}]")

def on_tool_call(call):
    print(f"\n[Calling tool: {call.name}]")
    result = execute_tool(call.name, call.args)
    agent.submit_tool_result(call.id, result)

response = agent.send_message(
    "Explain quantum entanglement",
    on_message=on_message,
    on_tool_call=on_tool_call
)
```

## Reconnection Handling

```python
from inferencesh import inference, StreamingOptions

client = inference(api_key="inf_...")

options = StreamingOptions(
    max_retries=3,
    retry_delay=1.0,  # seconds
    chunk_size=1024
)

for update in client.run(config, stream=True, options=options):
    print(update)
```

## Multiple Streams in Parallel

```python
from inferencesh import async_inference
import asyncio

async def run_parallel():
    client = async_inference(api_key="inf_...")

    configs = [
        {"app": "infsh/flux-schnell", "input": {"prompt": "A mountain"}},
        {"app": "infsh/flux-schnell", "input": {"prompt": "An ocean"}},
        {"app": "infsh/flux-schnell", "input": {"prompt": "A forest"}}
    ]

    async def stream_one(config, index):
        async for update in client.run(config, stream=True):
            print(f"[{index}] {update['status']}")
            if update.get("status") == "completed":
                return update.get("output")

    results = await asyncio.gather(*[
        stream_one(c, i) for i, c in enumerate(configs)
    ])
    return results

results = asyncio.run(run_parallel())
```

## Cancelling a Stream

```python
task_id = None

try:
    for update in client.run(config, stream=True):
        task_id = update.get("id")
        print(f"Status: {update['status']}")

        if should_cancel():
            break
finally:
    if task_id:
        client.cancel_task(task_id)
        print("Task cancelled")
```

## Collecting All Logs

```python
all_logs = []

for update in client.run(config, stream=True):
    if update.get("logs"):
        all_logs.extend(update["logs"])

    if update.get("status") == "completed":
        print("Final logs:")
        for log in all_logs:
            print(f"  {log}")
```

## Custom Stream Processing

```python
class StreamProcessor:
    def __init__(self):
        self.logs = []
        self.start_time = None
        self.end_time = None

    def process(self, update):
        if self.start_time is None:
            self.start_time = time.time()

        if update.get("logs"):
            self.logs.extend(update["logs"])

        if update.get("status") in ["completed", "failed"]:
            self.end_time = time.time()
            return True  # Done

        return False  # Continue

    @property
    def duration(self):
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

processor = StreamProcessor()

for update in client.run(config, stream=True):
    if processor.process(update):
        break

print(f"Duration: {processor.duration:.2f}s")
print(f"Logs: {len(processor.logs)}")
```
