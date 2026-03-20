<template>
  <div class="bot-reply">
    <TaskProgressCard
      v-if="reply.bpProgress"
      :bp="reply.bpProgress"
      @toggle-mode="handleToggleMode"
      @view-output="handleViewOutput"
    />
    <ReplyHeader :reply="reply" />
    <ThinkingBlock v-if="reply.thinking" :content="reply.thinking" :done="reply.thinkingDone" />
    <PlanChecklist v-if="reply.planChecklist" :steps="reply.planChecklist" />
    <StepCardList v-if="reply.stepCards.length" :cards="reply.stepCards" :agent-summaries="reply.agentSummaries" />
    <SummaryOutput v-if="reply.summaryText && !reply.bpSubtaskOutput" :content="reply.summaryText" />
    <SubtaskCompleteBlock
      v-if="reply.bpSubtaskOutput && reply.bpProgress?.runMode === 'manual'"
      :subtask-name="reply.bpProgress?.subtasks.find(s => s.id === reply.bpSubtaskOutput?.subtaskId)?.name ?? ''"
      :subtask-id="reply.bpSubtaskOutput.subtaskId"
      :instance-id="reply.bpProgress?.instanceId ?? ''"
      :is-last-subtask="(reply.bpProgress?.currentSubtaskIndex ?? 0) >= (reply.bpProgress?.subtasks.length ?? 1) - 1"
      :subtask-index="Math.max(0, reply.bpProgress?.subtasks.findIndex(s => s.id === reply.bpSubtaskOutput?.subtaskId) ?? 0)"
      :summary="reply.bpSubtaskOutput.summary"
      @view-output="handleViewOutput"
      @continue="handleContinue"
      @edit="handleEdit"
    />
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
import TaskProgressCard from './TaskProgressCard.vue'
import SubtaskCompleteBlock from './SubtaskCompleteBlock.vue'
import { useBestPracticeStore } from '@/stores/bestpractice'
import { useUIStore } from '@/stores/ui'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'
import { httpClient } from '@/api/http-client'
import { sseClient } from '@/api/sse-client'

defineProps<{ reply: ReplyState }>()

const bpStore = useBestPracticeStore()
const uiStore = useUIStore()

function handleToggleMode(mode: 'manual' | 'auto') {
  const inst = bpStore.activeInstance
  if (!inst) return
  httpClient.setBPRunMode(inst.instanceId, mode).catch(console.error)
}

function handleViewOutput(subtaskId: string) {
  const inst = bpStore.activeInstance
  if (!inst) return
  uiStore.openSubtaskOutput(inst.instanceId, subtaskId)
}

async function handleContinue() {
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()
  const msg = '进入下一步'
  const convId = sessionStore.activeSessionId
  console.log('[BP-DEBUG] handleContinue clicked, convId:', convId, 'isStreaming:', chatStore.isStreaming)
  chatStore.addUserMessage(msg)
  console.log('[BP-DEBUG] addUserMessage done, messages count:', chatStore.messages.length)
  if (convId) {
    try {
      console.log('[BP-DEBUG] sending message to backend...')
      await sseClient.sendMessage(msg, convId, { thinking_mode: 'auto' })
      console.log('[BP-DEBUG] sendMessage completed')
    } catch (e) {
      console.error('[BP-DEBUG] sendMessage FAILED:', e)
      alert('发送失败，请检查网络连接或重试')
      chatStore.cancelCurrentReply() // 回退状态
    }
  } else {
    console.warn('[BP-DEBUG] No convId! Cannot send message to backend')
  }
}

function handleEdit(subtaskId: string) {
  handleViewOutput(subtaskId)
}
</script>

<style scoped>
.bot-reply {
  padding: 14px 0;
  animation: fadeIn 0.4s var(--ease-out) both;
}
</style>
