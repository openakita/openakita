<template>
  <div class="bot-reply">
    <ReplyHeader :reply="reply" />
    <ThinkingBlock v-if="reply.thinking" :content="reply.thinking" :done="reply.thinkingDone" />
    <PlanChecklist v-if="reply.planChecklist" :steps="reply.planChecklist" />
    <StepCardList v-if="reply.stepCards.length" :cards="reply.stepCards" :agent-summaries="reply.agentSummaries" />
    <SummaryOutput v-if="reply.summaryText" :content="reply.summaryText" />
    <AskUserBlock v-if="reply.askUser" :ask="reply.askUser" />
  </div>
</template>

<script setup lang="ts">
import type { ReplyState } from '@/types'
import ReplyHeader from './ReplyHeader.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import PlanChecklist from './PlanChecklist.vue'
import StepCardList from './StepCardList.vue'
import SummaryOutput from './SummaryOutput.vue'
import AskUserBlock from './AskUserBlock.vue'

defineProps<{ reply: ReplyState }>()
</script>

<style scoped>
.bot-reply {
  padding: 14px 0;
  animation: fadeIn 0.4s var(--ease-out) both;
}
</style>
