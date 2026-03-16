<template>
  <div class="step-card-list">
    <template v-for="(item, i) in renderItems" :key="item.key">
      <StepCard v-if="item.type === 'card'" :card="item.card!" />
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

interface RenderItem {
  type: 'card' | 'summary'
  key: string
  card?: StepCardType
  agentId?: string
  summary?: string
}

const props = defineProps<{
  cards: StepCardType[]
  agentSummaries?: Record<string, string>
}>()

const renderItems = computed<RenderItem[]>(() => {
  const items: RenderItem[] = []
  const summaries = props.agentSummaries ?? {}

  for (let i = 0; i < props.cards.length; i++) {
    const card = props.cards[i]
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
