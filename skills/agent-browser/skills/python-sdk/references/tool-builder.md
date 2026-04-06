# Tool Builder Reference

Complete guide to building tools with the Python SDK.

## Parameter Types

### Basic Types

```python
from inferencesh import string, number, integer, boolean

# String parameter
name = string("The user's full name")

# Number (float)
score = number("Score between 0 and 1")

# Integer
count = integer("Number of items")

# Boolean
enabled = boolean("Whether feature is enabled")
```

### Enum Type

```python
from inferencesh import enum_of

priority = enum_of(
    ["low", "medium", "high", "critical"],
    "Task priority level"
)
```

### Array Type

```python
from inferencesh import array, string

# Array of strings
tags = array(string("Tag name"), "List of tags")

# Array of objects
items = array(
    obj({
        "name": string("Item name"),
        "qty": integer("Quantity")
    }),
    "List of items"
)
```

### Object Type

```python
from inferencesh import obj, string, integer, optional

address = obj({
    "street": string("Street address"),
    "city": string("City name"),
    "state": string("State code"),
    "zip": optional(string("ZIP code"))
}, "Mailing address")
```

### Optional Parameters

```python
from inferencesh import optional, string

# Optional string
nickname = optional(string("User's nickname"))
```

## Tool Types

### Client Tools

Tools that execute in your code:

```python
from inferencesh import tool, string, integer

# Basic tool
greet = (
    tool("greet")
    .describe("Greets a user")
    .param("name", string("Name to greet"))
    .build()
)

# Tool with multiple parameters
send_email = (
    tool("send_email")
    .display("Send Email")
    .describe("Sends an email to a recipient")
    .param("to", string("Recipient email"))
    .param("subject", string("Email subject"))
    .param("body", string("Email body"))
    .param("priority", integer("Priority 1-5"), default=3)
    .require_approval()
    .build()
)
```

### App Tools

Tools that call inference.sh apps:

```python
from inferencesh import app_tool, string

# Basic app tool
generate = (
    app_tool("generate_image", "infsh/flux-schnell@latest")
    .describe("Generate an image from a text prompt")
    .param("prompt", string("Image description"))
    .build()
)

# App tool with setup and defaults
translate = (
    app_tool("translate", "infsh/translator@latest")
    .describe("Translate text between languages")
    .param("text", string("Text to translate"))
    .param("target_lang", string("Target language code"))
    .setup({
        "model": "advanced",
        "preserve_formatting": True
    })
    .input({
        "source_lang": "auto"
    })
    .build()
)
```

### Agent Tools

Tools that delegate to other agents:

```python
from inferencesh import agent_tool, string

researcher = (
    agent_tool("research", "my-org/researcher@v1")
    .describe("Research a topic in depth")
    .param("topic", string("Topic to research"))
    .param("depth", string("Research depth: brief, moderate, comprehensive"))
    .build()
)

coder = (
    agent_tool("write_code", "my-org/coder@latest")
    .describe("Write code to solve a problem")
    .param("task", string("Coding task description"))
    .param("language", string("Programming language"))
    .build()
)
```

### Webhook Tools

Tools that call external HTTP endpoints:

```python
from inferencesh import webhook_tool, string

# Slack notification
slack = (
    webhook_tool("notify_slack", "https://hooks.slack.com/services/...")
    .describe("Send a message to Slack")
    .param("channel", string("Channel name"))
    .param("message", string("Message text"))
    .build()
)

# Webhook with secret
github = (
    webhook_tool("create_issue", "https://api.github.com/repos/org/repo/issues")
    .describe("Create a GitHub issue")
    .secret("GITHUB_TOKEN")  # Uses stored secret
    .param("title", string("Issue title"))
    .param("body", string("Issue description"))
    .build()
)
```

## Tool Builder Methods

### Common Methods

| Method | Description |
|--------|-------------|
| `.describe(text)` | Set tool description |
| `.display(name)` | Set display name |
| `.param(name, type, default=None)` | Add parameter |
| `.require_approval()` | Require human approval |
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

```python
from inferencesh import internal_tools

config = (
    internal_tools()
    .plan()                    # Task planning
    .memory()                  # Information storage
    .web_search(True)         # Web search capability
    .code_execution(True)     # Run code
    .image_generation({
        "enabled": True,
        "app_ref": "infsh/flux@latest"
    })
    .build()
)
```

### Internal Tool Options

| Tool | Description |
|------|-------------|
| `.plan()` | Enable task breakdown and planning |
| `.memory()` | Enable information storage |
| `.web_search(enabled)` | Enable/disable web search |
| `.code_execution(enabled)` | Enable/disable code running |
| `.image_generation(config)` | Configure image generation |

## Handling Tool Calls

### Basic Handler

```python
def handle_tool(call):
    if call.name == "greet":
        result = f"Hello, {call.args['name']}!"
    elif call.name == "calculate":
        result = eval(call.args['expression'])
    else:
        result = {"error": f"Unknown tool: {call.name}"}

    agent.submit_tool_result(call.id, result)

response = agent.send_message(
    "Greet John",
    on_tool_call=handle_tool
)
```

### With Approval

```python
def handle_tool(call):
    if call.requires_approval:
        print(f"Tool: {call.name}")
        print(f"Args: {call.args}")
        approved = input("Approve? (y/n): ").lower() == 'y'

        if not approved:
            agent.submit_tool_result(call.id, {
                "error": "Denied by user"
            })
            return

    result = execute_tool(call.name, call.args)
    agent.submit_tool_result(call.id, result)
```

### Widget Results

Return structured data for UI widgets:

```python
def handle_tool(call):
    if call.name == "confirm_order":
        # Return widget data
        agent.submit_tool_result(call.id, {
            "action": {"type": "confirm"},
            "form_data": {
                "order_id": "12345",
                "items": ["Widget A", "Widget B"],
                "total": 99.99
            }
        })
```

## Complete Example

```python
from inferencesh import (
    inference, tool, app_tool, webhook_tool,
    string, number, integer, boolean, enum_of,
    array, obj, optional, internal_tools
)

client = inference(api_key="inf_...")

# Calculator tool
calculator = (
    tool("calculate")
    .display("Calculator")
    .describe("Perform mathematical calculations")
    .param("expression", string("Math expression to evaluate"))
    .build()
)

# Image generation tool
image_gen = (
    app_tool("generate_image", "infsh/flux-schnell@latest")
    .describe("Generate an image from text")
    .param("prompt", string("Image description"))
    .param("style", enum_of(["realistic", "artistic", "cartoon"], "Image style"))
    .setup({"quality": "high"})
    .input({"steps": 20})
    .require_approval()
    .build()
)

# Slack notification tool
slack = (
    webhook_tool("notify", "https://hooks.slack.com/...")
    .describe("Send Slack notification")
    .param("message", string("Message to send"))
    .build()
)

# Built-in tools
internals = (
    internal_tools()
    .web_search(True)
    .code_execution(True)
    .build()
)

# Create agent with all tools
agent = client.agent({
    "core_app": {"ref": "infsh/claude-sonnet-4@latest"},
    "system_prompt": "You are a helpful assistant with various capabilities.",
    "tools": [calculator, image_gen, slack],
    "internal_tools": internals,
    "temperature": 0.7
})

# Handle tool calls
def handle_tool(call):
    if call.name == "calculate":
        try:
            result = eval(call.args["expression"])
            agent.submit_tool_result(call.id, {"result": result})
        except Exception as e:
            agent.submit_tool_result(call.id, {"error": str(e)})
    elif call.requires_approval:
        approved = input(f"Allow {call.name}? (y/n): ").lower() == 'y'
        if approved:
            # Let the app/webhook tool execute
            pass
        else:
            agent.submit_tool_result(call.id, {"error": "Denied"})

response = agent.send_message(
    "Calculate 15% tip on $85, then notify Slack",
    on_tool_call=handle_tool
)
```
