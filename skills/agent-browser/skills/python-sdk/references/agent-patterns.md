# Agent Patterns

Common patterns for building agents with the Python SDK.

## Multi-Agent Orchestration

Delegate tasks to specialized sub-agents:

```python
from inferencesh import inference, agent_tool, string

client = inference(api_key="inf_...")

# Define sub-agents as tools
researcher = (
    agent_tool("research", "my-org/researcher@latest")
    .describe("Research a topic thoroughly")
    .param("topic", string("Topic to research"))
    .build()
)

writer = (
    agent_tool("write", "my-org/writer@latest")
    .describe("Write content based on research")
    .param("outline", string("Content outline"))
    .param("research", string("Research findings"))
    .build()
)

# Orchestrator agent
orchestrator = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "system_prompt": """You are an orchestrator that:
1. Uses the research tool to gather information
2. Uses the write tool to create content
Coordinate between agents to produce high-quality output.""",
    "tools": [researcher, writer]
})

response = orchestrator.send_message("Create a blog post about AI agents")
```

## RAG Pattern (Retrieval-Augmented Generation)

Combine search with LLM responses:

```python
from inferencesh import inference, app_tool, string

client = inference(api_key="inf_...")

# Search tool
search = (
    app_tool("search", "tavily/search-assistant@latest")
    .describe("Search the web for current information")
    .param("query", string("Search query"))
    .build()
)

# RAG agent
rag_agent = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "system_prompt": """You help users with current information.
When asked about recent events or facts you're unsure about,
use the search tool to find accurate, up-to-date information.
Always cite your sources.""",
    "tools": [search]
})

response = rag_agent.send_message("What are the latest developments in quantum computing?")
```

## Code Execution Pattern

Agents that can write and run code:

```python
from inferencesh import inference, internal_tools

client = inference(api_key="inf_...")

config = (
    internal_tools()
    .code_execution(True)
    .build()
)

coder = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "system_prompt": """You are a Python coding assistant.
Write code to solve problems and execute it to verify it works.
Explain your approach and show the output.""",
    "internal_tools": config
})

response = coder.send_message("Calculate the first 20 Fibonacci numbers")
```

## Human-in-the-Loop Pattern

Require approval for sensitive operations:

```python
from inferencesh import inference, tool, string

client = inference(api_key="inf_...")

# Tool requiring approval
delete_file = (
    tool("delete_file")
    .describe("Delete a file from the filesystem")
    .param("path", string("File path to delete"))
    .require_approval()
    .build()
)

def handle_tool(call):
    if call.requires_approval:
        print(f"\n⚠️  Agent wants to: {call.name}")
        print(f"   Arguments: {call.args}")
        confirm = input("Allow? (y/n): ")

        if confirm.lower() == 'y':
            result = execute_operation(call.name, call.args)
            agent.submit_tool_result(call.id, result)
        else:
            agent.submit_tool_result(call.id, {
                "error": "Operation denied by user"
            })
    else:
        result = execute_operation(call.name, call.args)
        agent.submit_tool_result(call.id, result)

agent = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "tools": [delete_file]
})

response = agent.send_message(
    "Clean up temporary files in /tmp/myapp",
    on_tool_call=handle_tool
)
```

## Conversation Memory Pattern

Maintain context across sessions:

```python
import json
from inferencesh import inference

client = inference(api_key="inf_...")

def save_chat(agent, filepath):
    chat = agent.get_chat()
    with open(filepath, 'w') as f:
        json.dump(chat, f)

def load_chat(agent, filepath):
    try:
        with open(filepath, 'r') as f:
            chat = json.load(f)
            # Restore conversation by replaying messages
            for msg in chat['messages']:
                if msg['role'] == 'user':
                    agent.send_message(msg['content'])
    except FileNotFoundError:
        pass

agent = client.agent("my-org/assistant@latest")

# Load previous conversation
load_chat(agent, "conversation.json")

# Continue conversation
response = agent.send_message("Continue where we left off")

# Save for next session
save_chat(agent, "conversation.json")
```

## Streaming with Progress UI

Real-time updates for better UX:

```python
from inferencesh import inference
import sys

client = inference(api_key="inf_...")
agent = client.agent("my-org/assistant@latest")

def stream_handler(msg):
    if msg.get("content"):
        sys.stdout.write(msg["content"])
        sys.stdout.flush()

def tool_handler(call):
    print(f"\n🔧 Using tool: {call.name}")
    # Execute and return result
    result = execute_tool(call.name, call.args)
    agent.submit_tool_result(call.id, result)
    print("✅ Tool completed")

response = agent.send_message(
    "Generate a report on market trends",
    on_message=stream_handler,
    on_tool_call=tool_handler
)

print("\n\n📊 Report complete!")
```

## Error Recovery Pattern

Graceful handling of failures:

```python
from inferencesh import inference, RequirementsNotMetException
import time

client = inference(api_key="inf_...")

def robust_run(config, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.run(config)
        except RequirementsNotMetException as e:
            print(f"Missing requirements: {e.errors}")
            raise
        except RuntimeError as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"Error: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

result = robust_run({
    "app": "infsh/flux-schnell",
    "input": {"prompt": "A serene landscape"}
})
```

## Batch Processing Pattern

Process multiple items efficiently:

```python
from inferencesh import async_inference
import asyncio

async def process_batch(items):
    client = async_inference(api_key="inf_...")

    async def process_one(item):
        result = await client.run({
            "app": "infsh/flux-schnell",
            "input": {"prompt": item}
        })
        return result

    # Process in parallel with concurrency limit
    semaphore = asyncio.Semaphore(5)  # Max 5 concurrent

    async def bounded_process(item):
        async with semaphore:
            return await process_one(item)

    results = await asyncio.gather(*[
        bounded_process(item) for item in items
    ])
    return results

prompts = [
    "A mountain sunrise",
    "A city at night",
    "An ocean sunset",
    "A forest path"
]

results = asyncio.run(process_batch(prompts))
```
