<template>
  <div class="step-card-list">
    <template v-for="item in renderItems" :key="item.key">
      <ThinkingBlock
        v-if="item.type === 'thinking'"
        :content="item.thinkingContent!"
        :done="item.thinkingDone!"
      />
      <StepCard v-else-if="item.type === 'card'" :card="item.card!" />
      <AgentSummaryBlock
        v-else-if="item.type === 'summary'"
        :agent-id="item.agentId!"
        :summary="item.summary!"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { StepCard as StepCardType } from '@/types'
import StepCard from './StepCard.vue'
import AgentSummaryBlock from './AgentSummaryBlock.vue'
import ThinkingBlock from './ThinkingBlock.vue'

interface RenderItem {
  type: 'card' | 'summary' | 'thinking'
  key: string
  card?: StepCardType
  agentId?: string
  summary?: string
  thinkingContent?: string
  thinkingDone?: boolean
}

const props = defineProps<{
  cards: StepCardType[]
  agentSummaries?: Record<string, string>
  agentThinking?: Record<string, { content: string; done: boolean }>
}>()

const renderItems = computed<RenderItem[]>(() => {
  const items: RenderItem[] = []
  const summaries = props.agentSummaries ?? {}
  const thinking = props.agentThinking ?? {}
  const emittedThinking = new Set<string>()

  for (let i = 0; i < props.cards.length; i++) {
    const card = props.cards[i]

    // Insert thinking block before delegate card using delegateAgentId
    if (card.cardType === 'delegate' && card.delegateAgentId) {
      const dedupKey = card.stepId  // unique per subtask delegation
      if (!emittedThinking.has(dedupKey)) {
        const aid = card.delegateAgentId
        const at = thinking[aid]
        if (at && at.content) {
          emittedThinking.add(dedupKey)
          items.push({
            type: 'thinking',
            key: `thinking_${aid}_${i}`,
            thinkingContent: at.content,
            thinkingDone: at.done,
          })
        }
      }
    }

    // For non-BP sub-agents (no preceding delegate card), insert thinking before first card of group
    if (card.agentId && card.agentId !== 'main' && !emittedThinking.has(card.agentId)) {
      const prev = props.cards[i - 1]
      if (!prev || (prev.agentId !== card.agentId && prev.cardType !== 'delegate')) {
        const at = thinking[card.agentId]
        if (at && at.content) {
          emittedThinking.add(card.agentId)
          items.push({
            type: 'thinking',
            key: `thinking_${card.agentId}_${i}`,
            thinkingContent: at.content,
            thinkingDone: at.done,
          })
        }
      }
    }

    items.push({ type: 'card', key: card.stepId, card })

    // Detect end of sub-agent group: current card is sub-agent,
    // and next card is different agent or end of list
    if (card.agentId && card.agentId !== 'main') {
      const next = props.cards[i + 1]
      if (!next || next.agentId !== card.agentId) {
        const text = summaries[card.agentId]
        if (text) {
          items.push({
            type: 'summary',
            key: `summary_${card.agentId}_${i}`,
            agentId: card.agentId,
            summary: text,
          })
        }
      }
    }
  }
  return items
})
</script>

<style scoped>
.step-card-list { margin: 8px 0; }
</style>
