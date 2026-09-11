# Prompt caching and usage accounting

OpenAkita keeps common rules, safety instructions and the compiled identity at
the beginning of its generated system prompt. FULL-only behavior, persona and
mode instructions follow the shared prefix. Switching between FULL and MINIMAL
therefore preserves the common prefix, rather than inserting extra rules ahead
of the identity. Changes to the identity or model-specific base prompt can still
change that prefix.

`DYNAMIC_BOUNDARY` marks the end of the shared prefix for providers that support
explicit cache blocks. It does not itself enable caching on DeepSeek.

Current time, session metadata, working facts, retrieved memories and the user
profile are rendered into a separate context region, bounded by
`TURN_CONTEXT_BOUNDARY` and `TURN_CONTEXT_END`. Runtime instructions, project
policies, ask-user continuation rules, contradiction guards and appended agent
or plan policies remain system instructions. The whole generated prompt is
rebuilt for each turn; compiled assets and expensive host-environment discovery
retain their local caches. Time is rendered independently of the host cache.

For DeepSeek Chat Completions, the converter sends:

```text
system: shared instructions + current mode/runtime/tool policies
user/assistant/tool: previous history
user: [OpenAkita runtime context] current snapshot
user: current request
assistant/tool: current tool continuation, if any
```

The context snapshot is data, not a user request or permission to execute tools.
System instructions explicitly establish this distinction. The converter does
not edit stored messages or insert between an assistant tool call and its
results. It also does not append a partial system message: some models interpret
a later system message as a replacement for the complete system prompt.

Other providers retain their existing message-role layout. Custom prompts
without the context delimiters are unchanged. Plugin and agent policies appended
after the closing delimiter remain in the leading system message.

The snapshot is request-local; it is not persisted as an additional conversation
turn. This preserves earlier history as a reusable prefix but does not guarantee
that the previous request's entire input can be reused. Mode-specific rules,
tool schemas, plans, compaction and provider cache persistence/eviction can still
cause misses. Real cache improvements must be measured with repeated requests;
offline prefix tests do not predict a hit percentage.

## Usage counters

All three Chat Completions response paths (non-streaming, usage-only streaming
chunks and finish chunks) use the same parser. DeepSeek's
`prompt_cache_hit_tokens` is read alongside the OpenAI-compatible
`prompt_tokens_details.cached_tokens` and `cached_tokens` variants. The raw
DeepSeek hit field takes precedence when present, including an explicit zero.

For OpenAI Chat Completions and Responses, input tokens already include cache
reads. Cache reads are a subset, so costs charge ordinary input prices only for
`input - cache_read`, and token budgets count input once. Anthropic retains its
separate uncached-input and cache counters. Compiler endpoint calls use their
own endpoint configuration for accounting. Historical database rows are not
rewritten or backfilled when their original usage fields are unavailable.

For DeepSeek, the input cache-hit ratio is:

```text
sum(cache_read_tokens) / sum(input_tokens)
```

Output tokens are excluded from the denominator. For meaningful comparisons,
separate main replies from intent analysis, retrieval and other background calls.

References: [DeepSeek context caching](https://api-docs.deepseek.com/guides/kv_cache/),
[Chat Completions usage fields](https://api-docs.deepseek.com/api/create-chat-completion/).
