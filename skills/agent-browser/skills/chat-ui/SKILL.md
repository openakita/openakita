---
name: chat-ui
description: "Chat UI building blocks for React/Next.js from ui.inference.sh. Components: container, messages, input, typing indicators, avatars. Capabilities: chat interfaces, message lists, input handling, streaming. Use for: building custom chat UIs, messaging interfaces, AI assistants. Triggers: chat ui, chat component, message list, chat input, shadcn chat,"  react chat, chat interface, messaging ui, conversation ui, chat building blocks
---

# Chat UI Components

Chat building blocks from [ui.inference.sh](https://ui.inference.sh).

![Chat UI Components](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kgvftp7hb8wby7z66fvs9asd.jpeg)

## Quick Start

```bash
# Install chat components
npx shadcn@latest add https://ui.inference.sh/r/chat.json
```

## Components

### Chat Container

```tsx
import { ChatContainer } from "@/registry/blocks/chat/chat-container"

<ChatContainer>
  {/* messages go here */}
</ChatContainer>
```

### Messages

```tsx
import { ChatMessage } from "@/registry/blocks/chat/chat-message"

<ChatMessage
  role="user"
  content="Hello, how can you help me?"
/>

<ChatMessage
  role="assistant"
  content="I can help you with many things!"
/>
```

### Chat Input

```tsx
import { ChatInput } from "@/registry/blocks/chat/chat-input"

<ChatInput
  onSubmit={(message) => handleSend(message)}
  placeholder="Type a message..."
  disabled={isLoading}
/>
```

### Typing Indicator

```tsx
import { TypingIndicator } from "@/registry/blocks/chat/typing-indicator"

{isTyping && <TypingIndicator />}
```

## Full Example

```tsx
import {
  ChatContainer,
  ChatMessage,
  ChatInput,
  TypingIndicator,
} from "@/registry/blocks/chat"

export function Chat() {
  const [messages, setMessages] = useState([])
  const [isLoading, setIsLoading] = useState(false)

  const handleSend = async (content: string) => {
    setMessages(prev => [...prev, { role: 'user', content }])
    setIsLoading(true)
    // Send to API...
    setIsLoading(false)
  }

  return (
    <ChatContainer>
      {messages.map((msg, i) => (
        <ChatMessage key={i} role={msg.role} content={msg.content} />
      ))}
      {isLoading && <TypingIndicator />}
      <ChatInput onSubmit={handleSend} disabled={isLoading} />
    </ChatContainer>
  )
}
```

## Message Variants

| Role | Description |
|------|-------------|
| `user` | User messages (right-aligned) |
| `assistant` | AI responses (left-aligned) |
| `system` | System messages (centered) |

## Styling

Components use Tailwind CSS and shadcn/ui design tokens:

```tsx
<ChatMessage
  role="assistant"
  content="Hello!"
  className="bg-muted"
/>
```

## Related Skills

```bash
# Full agent component (recommended)
npx skills add inference-sh/skills@agent-ui

# Declarative widgets
npx skills add inference-sh/skills@widgets-ui

# Markdown rendering
npx skills add inference-sh/skills@markdown-ui
```

## Documentation

- [Chatting with Agents](https://inference.sh/docs/agents/chatting) - Building chat interfaces
- [Agent UX Patterns](https://inference.sh/blog/ux/agent-ux-patterns) - Chat UX best practices
- [Real-Time Streaming](https://inference.sh/blog/observability/streaming) - Streaming responses

Component docs: [ui.inference.sh/blocks/chat](https://ui.inference.sh/blocks/chat)
