---
name: python-sdk
description: "Python SDK for inference.sh - run AI apps, build agents, and integrate with 150+ models. Package: inferencesh (pip install inferencesh). Supports sync/async, streaming, file uploads. Build agents with template or ad-hoc patterns, tool builder API, skills, and human approval. Use for: Python integration, AI apps, agent development, RAG pipelines, automation. Triggers: python sdk, inferencesh, pip install, python api, python client, async inference, python agent, tool builder python, programmatic ai, python integration, sdk python"
allowed-tools: Bash(pip install inferencesh), Bash(python *)
---

# Python SDK

Build AI applications with the [inference.sh](https://inference.sh) Python SDK.

![Python SDK](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kgvftjwhby36trvaj66bwzcf.jpeg)

## Quick Start

```bash
pip install inferencesh
```

```python
from inferencesh import inference

client = inference(api_key="inf_your_key")

# Run an AI app
result = client.run({
    "app": "infsh/flux-schnell",
    "input": {"prompt": "A sunset over mountains"}
})
print(result["output"])
```

## Installation

```bash
# Standard installation
pip install inferencesh

# With async support
pip install inferencesh[async]
```

**Requirements:** Python 3.8+

## Authentication

```python
import os
from inferencesh import inference

# Direct API key
client = inference(api_key="inf_your_key")

# From environment variable (recommended)
client = inference(api_key=os.environ["INFERENCE_API_KEY"])
```

Get your API key: Settings → API Keys → Create API Key

## Running Apps

### Basic Execution

```python
result = client.run({
    "app": "infsh/flux-schnell",
    "input": {"prompt": "A cat astronaut"}
})

print(result["status"])  # "completed"
print(result["output"])  # Output data
```

### Fire and Forget

```python
task = client.run({
    "app": "google/veo-3-1-fast",
    "input": {"prompt": "Drone flying over mountains"}
}, wait=False)

print(f"Task ID: {task['id']}")
# Check later with client.get_task(task['id'])
```

### Streaming Progress

```python
for update in client.run({
    "app": "google/veo-3-1-fast",
    "input": {"prompt": "Ocean waves at sunset"}
}, stream=True):
    print(f"Status: {update['status']}")
    if update.get("logs"):
        print(update["logs"][-1])
```

### Run Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `app` | string | App ID (namespace/name@version) |
| `input` | dict | Input matching app schema |
| `setup` | dict | Hidden setup configuration |
| `infra` | string | 'cloud' or 'private' |
| `session` | string | Session ID for stateful execution |
| `session_timeout` | int | Idle timeout (1-3600 seconds) |

## File Handling

### Automatic Upload

```python
result = client.run({
    "app": "image-processor",
    "input": {
        "image": "/path/to/image.png"  # Auto-uploaded
    }
})
```

### Manual Upload

```python
from inferencesh import UploadFileOptions

# Basic upload
file = client.upload_file("/path/to/image.png")

# With options
file = client.upload_file(
    "/path/to/image.png",
    UploadFileOptions(
        filename="custom_name.png",
        content_type="image/png",
        public=True
    )
)

result = client.run({
    "app": "image-processor",
    "input": {"image": file["uri"]}
})
```

## Sessions (Stateful Execution)

Keep workers warm across multiple calls:

```python
# Start new session
result = client.run({
    "app": "my-app",
    "input": {"action": "init"},
    "session": "new",
    "session_timeout": 300  # 5 minutes
})
session_id = result["session_id"]

# Continue in same session
result = client.run({
    "app": "my-app",
    "input": {"action": "process"},
    "session": session_id
})
```

## Agent SDK

### Template Agents

Use pre-built agents from your workspace:

```python
agent = client.agent("my-team/support-agent@latest")

# Send message
response = agent.send_message("Hello!")
print(response.text)

# Multi-turn conversation
response = agent.send_message("Tell me more")

# Reset conversation
agent.reset()

# Get chat history
chat = agent.get_chat()
```

### Ad-hoc Agents

Create custom agents programmatically:

```python
from inferencesh import tool, string, number, app_tool

# Define tools
calculator = (
    tool("calculate")
    .describe("Perform a calculation")
    .param("expression", string("Math expression"))
    .build()
)

image_gen = (
    app_tool("generate_image", "infsh/flux-schnell@latest")
    .describe("Generate an image")
    .param("prompt", string("Image description"))
    .build()
)

# Create agent
agent = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "system_prompt": "You are a helpful assistant.",
    "tools": [calculator, image_gen],
    "temperature": 0.7,
    "max_tokens": 4096
})

response = agent.send_message("What is 25 * 4?")
```

### Available Core Apps

| Model | App Reference |
|-------|---------------|
| Claude Sonnet 4 | `infsh/claude-sonnet-4@latest` |
| Claude 3.5 Haiku | `infsh/claude-haiku-35@latest` |
| GPT-4o | `infsh/gpt-4o@latest` |
| GPT-4o Mini | `infsh/gpt-4o-mini@latest` |

## Tool Builder API

### Parameter Types

```python
from inferencesh import (
    string, number, integer, boolean,
    enum_of, array, obj, optional
)

name = string("User's name")
age = integer("Age in years")
score = number("Score 0-1")
active = boolean("Is active")
priority = enum_of(["low", "medium", "high"], "Priority")
tags = array(string("Tag"), "List of tags")
address = obj({
    "street": string("Street"),
    "city": string("City"),
    "zip": optional(string("ZIP"))
}, "Address")
```

### Client Tools (Run in Your Code)

```python
greet = (
    tool("greet")
    .display("Greet User")
    .describe("Greets a user by name")
    .param("name", string("Name to greet"))
    .require_approval()
    .build()
)
```

### App Tools (Call AI Apps)

```python
generate = (
    app_tool("generate_image", "infsh/flux-schnell@latest")
    .describe("Generate an image from text")
    .param("prompt", string("Image description"))
    .setup({"model": "schnell"})
    .input({"steps": 20})
    .require_approval()
    .build()
)
```

### Agent Tools (Delegate to Sub-agents)

```python
from inferencesh import agent_tool

researcher = (
    agent_tool("research", "my-org/researcher@v1")
    .describe("Research a topic")
    .param("topic", string("Topic to research"))
    .build()
)
```

### Webhook Tools (Call External APIs)

```python
from inferencesh import webhook_tool

notify = (
    webhook_tool("slack", "https://hooks.slack.com/...")
    .describe("Send Slack notification")
    .secret("SLACK_SECRET")
    .param("channel", string("Channel"))
    .param("message", string("Message"))
    .build()
)
```

### Internal Tools (Built-in Capabilities)

```python
from inferencesh import internal_tools

config = (
    internal_tools()
    .plan()
    .memory()
    .web_search(True)
    .code_execution(True)
    .image_generation({
        "enabled": True,
        "app_ref": "infsh/flux@latest"
    })
    .build()
)

agent = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "internal_tools": config
})
```

## Streaming Agent Responses

```python
def handle_message(msg):
    if msg.get("content"):
        print(msg["content"], end="", flush=True)

def handle_tool(call):
    print(f"\n[Tool: {call.name}]")
    result = execute_tool(call.name, call.args)
    agent.submit_tool_result(call.id, result)

response = agent.send_message(
    "Explain quantum computing",
    on_message=handle_message,
    on_tool_call=handle_tool
)
```

## File Attachments

```python
# From file path
with open("image.png", "rb") as f:
    response = agent.send_message(
        "What's in this image?",
        files=[f.read()]
    )

# From base64
response = agent.send_message(
    "Analyze this",
    files=["data:image/png;base64,iVBORw0KGgo..."]
)
```

## Skills (Reusable Context)

```python
agent = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "skills": [
        {
            "name": "code-review",
            "description": "Code review guidelines",
            "content": "# Code Review\n\n1. Check security\n2. Check performance..."
        },
        {
            "name": "api-docs",
            "description": "API documentation",
            "url": "https://example.com/skills/api-docs.md"
        }
    ]
})
```

## Async Support

```python
from inferencesh import async_inference
import asyncio

async def main():
    client = async_inference(api_key="inf_...")

    # Async app execution
    result = await client.run({
        "app": "infsh/flux-schnell",
        "input": {"prompt": "A galaxy"}
    })

    # Async agent
    agent = client.agent("my-org/assistant@latest")
    response = await agent.send_message("Hello!")

    # Async streaming
    async for msg in agent.stream_messages():
        print(msg)

asyncio.run(main())
```

## Error Handling

```python
from inferencesh import RequirementsNotMetException

try:
    result = client.run({"app": "my-app", "input": {...}})
except RequirementsNotMetException as e:
    print(f"Missing requirements:")
    for err in e.errors:
        print(f"  - {err['type']}: {err['key']}")
except RuntimeError as e:
    print(f"Error: {e}")
```

## Human Approval Workflows

```python
def handle_tool(call):
    if call.requires_approval:
        # Show to user, get confirmation
        approved = prompt_user(f"Allow {call.name}?")
        if approved:
            result = execute_tool(call.name, call.args)
            agent.submit_tool_result(call.id, result)
        else:
            agent.submit_tool_result(call.id, {"error": "Denied by user"})

response = agent.send_message(
    "Delete all temp files",
    on_tool_call=handle_tool
)
```

## Reference Files

- [Agent Patterns](references/agent-patterns.md) - Multi-agent, RAG, human-in-the-loop patterns
- [Tool Builder](references/tool-builder.md) - Complete tool builder API reference
- [Streaming](references/streaming.md) - Real-time progress updates and SSE handling
- [File Handling](references/files.md) - Upload, download, and manage files
- [Sessions](references/sessions.md) - Stateful execution with warm workers
- [Async Patterns](references/async-patterns.md) - Parallel processing and async/await

## Related Skills

```bash
# JavaScript SDK
npx skills add inference-sh/skills@javascript-sdk

# Full platform skill (all 150+ apps via CLI)
npx skills add inference-sh/skills@inference-sh

# LLM models
npx skills add inference-sh/skills@llm-models

# Image generation
npx skills add inference-sh/skills@ai-image-generation
```

## Documentation

- [Python SDK Reference](https://inference.sh/docs/api/sdk-python) - Full API documentation
- [Agent SDK Overview](https://inference.sh/docs/api/agent-sdk) - Building agents
- [Tool Builder Reference](https://inference.sh/docs/api/agent-tools) - Creating tools
- [Authentication](https://inference.sh/docs/api/authentication) - API key setup
- [Streaming](https://inference.sh/docs/api/sdk/streaming) - Real-time updates
- [File Uploads](https://inference.sh/docs/api/sdk/files) - File handling
