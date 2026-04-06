# Async Patterns Reference

Asynchronous programming with the Python SDK.

## Basic Async Client

```python
from inferencesh import async_inference
import asyncio

async def main():
    client = async_inference(api_key="inf_...")

    result = await client.run({
        "app": "infsh/flux-schnell",
        "input": {"prompt": "A sunset"}
    })
    print(result["output"])

asyncio.run(main())
```

## Parallel Requests

Run multiple independent requests simultaneously:

```python
async def parallel_requests():
    client = async_inference(api_key="inf_...")

    prompts = [
        "A mountain landscape",
        "An ocean sunset",
        "A forest path",
        "A city skyline"
    ]

    # Create all tasks
    tasks = [
        client.run({
            "app": "infsh/flux-schnell",
            "input": {"prompt": p}
        })
        for p in prompts
    ]

    # Run in parallel
    results = await asyncio.gather(*tasks)

    return results

results = asyncio.run(parallel_requests())
```

## Controlled Concurrency

Limit concurrent requests with semaphore:

```python
async def controlled_concurrency(items, max_concurrent=5):
    client = async_inference(api_key="inf_...")
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_one(item):
        async with semaphore:
            return await client.run({
                "app": "processor",
                "input": {"data": item}
            })

    tasks = [process_one(item) for item in items]
    return await asyncio.gather(*tasks)

# Process 100 items, max 5 at a time
results = asyncio.run(controlled_concurrency(range(100), max_concurrent=5))
```

## Async Streaming

```python
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

## Async Agents

```python
async def agent_conversation():
    client = async_inference(api_key="inf_...")
    agent = client.agent("my-org/assistant@latest")

    # Send message
    response = await agent.send_message("Hello!")
    print(response.text)

    # Streaming with callback
    async def on_message(msg):
        if msg.get("content"):
            print(msg["content"], end="", flush=True)

    response = await agent.send_message(
        "Tell me a story",
        on_message=on_message
    )

asyncio.run(agent_conversation())
```

## Producer-Consumer Pattern

```python
async def producer_consumer():
    client = async_inference(api_key="inf_...")
    queue = asyncio.Queue()
    results = []

    async def producer(items):
        for item in items:
            await queue.put(item)
        # Signal end
        for _ in range(3):  # Number of consumers
            await queue.put(None)

    async def consumer(consumer_id):
        while True:
            item = await queue.get()
            if item is None:
                break

            result = await client.run({
                "app": "processor",
                "input": {"data": item}
            })
            results.append((item, result))
            print(f"Consumer {consumer_id} processed {item}")

    items = list(range(20))

    # Start producer and consumers
    await asyncio.gather(
        producer(items),
        consumer(1),
        consumer(2),
        consumer(3)
    )

    return results

results = asyncio.run(producer_consumer())
```

## Timeout Handling

```python
async def with_timeout():
    client = async_inference(api_key="inf_...")

    try:
        result = await asyncio.wait_for(
            client.run({
                "app": "slow-app",
                "input": {"data": "..."}
            }),
            timeout=30.0  # 30 seconds
        )
        return result
    except asyncio.TimeoutError:
        print("Request timed out")
        return None

result = asyncio.run(with_timeout())
```

## Retry with Backoff

```python
async def retry_with_backoff(client, config, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await client.run(config)
        except Exception as e:
            if attempt == max_retries - 1:
                raise

            wait = (2 ** attempt) + random.random()
            print(f"Attempt {attempt + 1} failed, retrying in {wait:.1f}s...")
            await asyncio.sleep(wait)

async def main():
    client = async_inference(api_key="inf_...")

    result = await retry_with_backoff(client, {
        "app": "my-app",
        "input": {"data": "..."}
    })

asyncio.run(main())
```

## Batch Processing with Progress

```python
from tqdm.asyncio import tqdm

async def batch_with_progress(items):
    client = async_inference(api_key="inf_...")
    semaphore = asyncio.Semaphore(10)

    async def process_one(item):
        async with semaphore:
            return await client.run({
                "app": "processor",
                "input": {"data": item}
            })

    tasks = [process_one(item) for item in items]

    results = []
    for coro in tqdm.as_completed(tasks, desc="Processing"):
        result = await coro
        results.append(result)

    return results

results = asyncio.run(batch_with_progress(range(100)))
```

## Context Manager Pattern

```python
class AsyncInferenceSession:
    def __init__(self, api_key):
        self.api_key = api_key
        self.client = None

    async def __aenter__(self):
        self.client = async_inference(api_key=self.api_key)
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Cleanup if needed
        pass

async def main():
    async with AsyncInferenceSession("inf_...") as client:
        result = await client.run({
            "app": "my-app",
            "input": {"data": "..."}
        })
        print(result)

asyncio.run(main())
```

## Error Aggregation

```python
async def process_with_errors(items):
    client = async_inference(api_key="inf_...")

    async def safe_process(item):
        try:
            result = await client.run({
                "app": "processor",
                "input": {"data": item}
            })
            return {"success": True, "item": item, "result": result}
        except Exception as e:
            return {"success": False, "item": item, "error": str(e)}

    tasks = [safe_process(item) for item in items]
    results = await asyncio.gather(*tasks)

    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]

    print(f"Succeeded: {len(successes)}, Failed: {len(failures)}")

    return successes, failures

asyncio.run(process_with_errors(range(50)))
```

## Async Generator Pattern

```python
async def stream_results(items):
    """Yield results as they complete."""
    client = async_inference(api_key="inf_...")

    pending = set()

    for item in items:
        task = asyncio.create_task(client.run({
            "app": "processor",
            "input": {"data": item}
        }))
        task.item = item
        pending.add(task)

        # Limit pending tasks
        if len(pending) >= 10:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                yield task.item, await task

    # Wait for remaining
    while pending:
        done, pending = await asyncio.wait(
            pending,
            return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            yield task.item, await task

async def main():
    async for item, result in stream_results(range(100)):
        print(f"Completed: {item}")

asyncio.run(main())
```

## Integration with Web Frameworks

### FastAPI

```python
from fastapi import FastAPI
from inferencesh import async_inference

app = FastAPI()
client = async_inference(api_key="inf_...")

@app.post("/generate")
async def generate(prompt: str):
    result = await client.run({
        "app": "infsh/flux-schnell",
        "input": {"prompt": prompt}
    })
    return {"image": result["output"]["url"]}
```

### aiohttp

```python
from aiohttp import web
from inferencesh import async_inference

client = async_inference(api_key="inf_...")

async def handle_generate(request):
    data = await request.json()
    result = await client.run({
        "app": "infsh/flux-schnell",
        "input": {"prompt": data["prompt"]}
    })
    return web.json_response({"image": result["output"]["url"]})

app = web.Application()
app.router.add_post('/generate', handle_generate)
```
