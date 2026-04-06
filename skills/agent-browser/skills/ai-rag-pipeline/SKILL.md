---
name: ai-rag-pipeline
description: "Build RAG (Retrieval Augmented Generation) pipelines with web search and LLMs. Tools: Tavily Search, Exa Search, Exa Answer, Claude, GPT-4, Gemini via OpenRouter. Capabilities: research, fact-checking, grounded responses, knowledge retrieval. Use for: AI agents, research assistants, fact-checkers, knowledge bases. Triggers: rag, retrieval augmented generation, grounded ai, search and answer, research agent, fact checking, knowledge retrieval, ai research, search + llm, web grounded, perplexity alternative, ai with sources, citation, research pipeline"
allowed-tools: Bash(infsh *)
---

# AI RAG Pipeline

Build RAG (Retrieval Augmented Generation) pipelines via [inference.sh](https://inference.sh) CLI.

![AI RAG Pipeline](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kgndqjxd780zm2j3rmada6y8.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Simple RAG: Search + LLM
SEARCH=$(infsh app run tavily/search-assistant --input '{"query": "latest AI developments 2024"}')
infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"Based on this research, summarize the key trends: $SEARCH\"
}"
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## What is RAG?

RAG combines:
1. **Retrieval**: Fetch relevant information from external sources
2. **Augmentation**: Add retrieved context to the prompt
3. **Generation**: LLM generates response using the context

This produces more accurate, up-to-date, and verifiable AI responses.

## RAG Pipeline Patterns

### Pattern 1: Simple Search + Answer

```
[User Query] -> [Web Search] -> [LLM with Context] -> [Answer]
```

### Pattern 2: Multi-Source Research

```
[Query] -> [Multiple Searches] -> [Aggregate] -> [LLM Analysis] -> [Report]
```

### Pattern 3: Extract + Process

```
[URLs] -> [Content Extraction] -> [Chunking] -> [LLM Summary] -> [Output]
```

## Available Tools

### Search Tools

| Tool | App ID | Best For |
|------|--------|----------|
| Tavily Search | `tavily/search-assistant` | AI-powered search with answers |
| Exa Search | `exa/search` | Neural search, semantic matching |
| Exa Answer | `exa/answer` | Direct factual answers |

### Extraction Tools

| Tool | App ID | Best For |
|------|--------|----------|
| Tavily Extract | `tavily/extract` | Clean content from URLs |
| Exa Extract | `exa/extract` | Analyze web content |

### LLM Tools

| Model | App ID | Best For |
|-------|--------|----------|
| Claude Sonnet 4.5 | `openrouter/claude-sonnet-45` | Complex analysis |
| Claude Haiku 4.5 | `openrouter/claude-haiku-45` | Fast processing |
| GPT-4o | `openrouter/gpt-4o` | General purpose |
| Gemini 2.5 Pro | `openrouter/gemini-25-pro` | Long context |

## Pipeline Examples

### Basic RAG Pipeline

```bash
# 1. Search for information
SEARCH_RESULT=$(infsh app run tavily/search-assistant --input '{
  "query": "What are the latest breakthroughs in quantum computing 2024?"
}')

# 2. Generate grounded response
infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"You are a research assistant. Based on the following search results, provide a comprehensive summary with citations.

Search Results:
$SEARCH_RESULT

Provide a well-structured summary with source citations.\"
}"
```

### Multi-Source Research

```bash
# Search multiple sources
TAVILY=$(infsh app run tavily/search-assistant --input '{"query": "electric vehicle market trends 2024"}')
EXA=$(infsh app run exa/search --input '{"query": "EV market analysis latest reports"}')

# Combine and analyze
infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"Analyze these research results and identify common themes and contradictions.

Source 1 (Tavily):
$TAVILY

Source 2 (Exa):
$EXA

Provide a balanced analysis with sources.\"
}"
```

### URL Content Analysis

```bash
# 1. Extract content from specific URLs
CONTENT=$(infsh app run tavily/extract --input '{
  "urls": [
    "https://example.com/research-paper",
    "https://example.com/industry-report"
  ]
}')

# 2. Analyze extracted content
infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"Analyze these documents and extract key insights:

$CONTENT

Provide:
1. Key findings
2. Data points
3. Recommendations\"
}"
```

### Fact-Checking Pipeline

```bash
# Claim to verify
CLAIM="AI will replace 50% of jobs by 2030"

# 1. Search for evidence
EVIDENCE=$(infsh app run tavily/search-assistant --input "{
  \"query\": \"$CLAIM evidence studies research\"
}")

# 2. Verify claim
infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"Fact-check this claim: '$CLAIM'

Based on the following evidence:
$EVIDENCE

Provide:
1. Verdict (True/False/Partially True/Unverified)
2. Supporting evidence
3. Contradicting evidence
4. Sources\"
}"
```

### Research Report Generator

```bash
TOPIC="Impact of generative AI on creative industries"

# 1. Initial research
OVERVIEW=$(infsh app run tavily/search-assistant --input "{\"query\": \"$TOPIC overview\"}")
STATISTICS=$(infsh app run exa/search --input "{\"query\": \"$TOPIC statistics data\"}")
OPINIONS=$(infsh app run tavily/search-assistant --input "{\"query\": \"$TOPIC expert opinions\"}")

# 2. Generate comprehensive report
infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"Generate a comprehensive research report on: $TOPIC

Research Data:
== Overview ==
$OVERVIEW

== Statistics ==
$STATISTICS

== Expert Opinions ==
$OPINIONS

Format as a professional report with:
- Executive Summary
- Key Findings
- Data Analysis
- Expert Perspectives
- Conclusion
- Sources\"
}"
```

### Quick Answer with Sources

```bash
# Use Exa Answer for direct factual questions
infsh app run exa/answer --input '{
  "question": "What is the current market cap of NVIDIA?"
}'
```

## Best Practices

### 1. Query Optimization

```bash
# Bad: Too vague
"AI news"

# Good: Specific and contextual
"latest developments in large language models January 2024"
```

### 2. Context Management

```bash
# Summarize long search results before sending to LLM
SEARCH=$(infsh app run tavily/search-assistant --input '{"query": "..."}')

# If too long, summarize first
SUMMARY=$(infsh app run openrouter/claude-haiku-45 --input "{
  \"prompt\": \"Summarize these search results in bullet points: $SEARCH\"
}")

# Then use summary for analysis
infsh app run openrouter/claude-sonnet-45 --input "{
  \"prompt\": \"Based on this research summary, provide insights: $SUMMARY\"
}"
```

### 3. Source Attribution

Always ask the LLM to cite sources:

```bash
infsh app run openrouter/claude-sonnet-45 --input '{
  "prompt": "... Always cite sources in [Source Name](URL) format."
}'
```

### 4. Iterative Research

```bash
# First pass: broad search
INITIAL=$(infsh app run tavily/search-assistant --input '{"query": "topic overview"}')

# Second pass: dive deeper based on findings
DEEP=$(infsh app run tavily/search-assistant --input '{"query": "specific aspect from initial search"}')
```

## Pipeline Templates

### Agent Research Tool

```bash
#!/bin/bash
# research.sh - Reusable research function

research() {
  local query="$1"

  # Search
  local results=$(infsh app run tavily/search-assistant --input "{\"query\": \"$query\"}")

  # Analyze
  infsh app run openrouter/claude-haiku-45 --input "{
    \"prompt\": \"Summarize: $results\"
  }"
}

research "your query here"
```

## Related Skills

```bash
# Web search tools
npx skills add inference-sh/skills@web-search

# LLM models
npx skills add inference-sh/skills@llm-models

# Content pipelines
npx skills add inference-sh/skills@ai-content-pipeline

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

Browse all apps: `infsh app list`

## Documentation

- [Adding Tools to Agents](https://inference.sh/docs/agents/adding-tools) - Agent tool integration
- [Building a Research Agent](https://inference.sh/blog/guides/research-agent) - Full guide
