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
      :disabled="!reply.isDone"
      @view-output="handleViewOutput"
      @continue="handleContinue"
      @edit="handleEdit"
    />
    <BPInstanceCreatedBlock
      v-if="reply.bpInstanceCreated"
      :bp="reply.bpInstanceCreated"
      :disabled="!reply.isDone"
      @start="handleBpStart"
    />

    <BPAskUserBlock
      v-if="reply.bpAskUser"
      :ask-user="reply.bpAskUser"
      @submit="handleBpAnswer"
    />

    <BPOfferBlock
      v-if="reply.bpOffer"
      :offer="reply.bpOffer"
      :disabled="!reply.isDone"
      @accept="handleBpOfferAccept"
      @decline="handleBpOfferDecline"
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
import BPInstanceCreatedBlock from './BPInstanceCreatedBlock.vue'
import BPAskUserBlock from './BPAskUserBlock.vue'
import BPOfferBlock from './BPOfferBlock.vue'
import { useBestPracticeStore } from '@/stores/bestpractice'
import { useUIStore } from '@/stores/ui'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'
import { httpClient } from '@/api/http-client'
import { sseClient } from '@/api/sse-client'

const props = defineProps<{ reply: ReplyState }>()

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
  chatStore.addUserMessage(msg)
  if (chatStore.currentReply && bpStore.activeInstance) {
    chatStore.currentReply.bpProgress = bpStore.activeInstance
  }
  const inst = bpStore.activeInstance
  if (!inst) {
    console.warn('[BP] handleContinue: no active BP instance')
    return
  }
  try {
    await sseClient.streamBP('/api/bp/next', {
      instance_id: inst.instanceId,
      session_id: sessionStore.activeSessionId,
    })
  } catch (e) {
    console.error('[BP] handleContinue error:', e)
    alert('发送失败，请检查网络连接或重试')
    chatStore.cancelCurrentReply()
  }
}

async function handleBpStart() {
  if (!props.reply.bpInstanceCreated) return
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()
  chatStore.addUserMessage('开始执行')
  if (chatStore.currentReply && bpStore.activeInstance) {
    chatStore.currentReply.bpProgress = bpStore.activeInstance
  }
  try {
    await sseClient.streamBP('/api/bp/next', {
      instance_id: props.reply.bpInstanceCreated.instanceId,
      session_id: sessionStore.activeSessionId,
    })
  } catch (err) {
    console.error('[BP] start error:', err)
  }
}

async function handleBpAnswer(data: Record<string, unknown>) {
  if (!props.reply.bpAskUser) return
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()
  chatStore.addUserMessage(`补充数据: ${JSON.stringify(data).slice(0, 100)}`)
  try {
    await sseClient.streamBP('/api/bp/answer', {
      instance_id: props.reply.bpAskUser.instanceId,
      subtask_id: props.reply.bpAskUser.subtaskId,
      data,
      session_id: sessionStore.activeSessionId,
    })
  } catch (err) {
    console.error('[BP] answer error:', err)
  }
}

function handleEdit(subtaskId: string) {
  handleViewOutput(subtaskId)
}

async function handleBpOfferAccept(bpId: string) {
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()
  chatStore.addUserMessage('最佳实践模式')
  try {
    await sseClient.streamBP('/api/bp/start', {
      bp_id: bpId,
      session_id: sessionStore.activeSessionId,
      input_data: {},
      run_mode: 'manual',
    })
  } catch (err) {
    console.error('[BP] offer accept error:', err)
  }
}

async function handleBpOfferDecline() {
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()
  chatStore.addUserMessage('自由模式')
  try {
    await sseClient.sendMessage('自由模式', sessionStore.activeSessionId!)
  } catch (err) {
    console.error('[BP] offer decline error:', err)
  }
}
</script>

<style scoped>
.bot-reply {
  padding: 14px 0;
  animation: fadeIn 0.4s var(--ease-out) both;
}
</style>
