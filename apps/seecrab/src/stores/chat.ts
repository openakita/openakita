// apps/seecrab/src/stores/chat.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Message, ReplyState, StepCard, PlanStep, SSEEvent } from '@/types'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const currentReply = ref<ReplyState | null>(null)
  const isStreaming = ref(false)

  function startNewReply(replyId: string) {
    currentReply.value = {
      replyId,
      agentId: 'main',
      agentName: 'OpenAkita',
      thinking: '',
      thinkingDone: false,
      planChecklist: null,
      stepCards: [],
      summaryText: '',
      timer: {
        ttft: { state: 'idle', value: null },
        total: { state: 'idle', value: null },
      },
      askUser: null,
      artifacts: [],
      isDone: false,
    }
    isStreaming.value = true
  }

  function dispatchEvent(event: SSEEvent) {
    if (!currentReply.value) {
      startNewReply(`reply_${Date.now()}`)
    }
    const reply = currentReply.value!

    switch (event.type) {
      case 'thinking':
        reply.thinking += (event as any).content ?? ''
        break

      case 'step_card':
        _upsertStepCard(reply, event as any)
        break

      case 'ai_text':
        reply.summaryText += (event as any).content ?? ''
        break

      case 'timer_update':
        _handleTimer(reply, event)
        break

      case 'plan_checklist':
        reply.planChecklist = (event as any).steps as PlanStep[]
        break

      case 'ask_user':
        reply.askUser = {
          ask_id: (event as any).ask_id ?? '',
          question: (event as any).question,
          options: (event as any).options,
          answered: false,
        }
        break

      case 'agent_header':
        reply.agentId = (event as any).agent_id ?? 'main'
        reply.agentName = (event as any).agent_name ?? 'Agent'
        break

      case 'artifact':
        reply.artifacts.push(event as any)
        break

      case 'done':
        reply.isDone = true
        reply.thinkingDone = true
        isStreaming.value = false
        messages.value.push({
          id: reply.replyId,
          role: 'assistant',
          content: reply.summaryText,
          timestamp: Date.now(),
          reply: { ...reply },
        })
        currentReply.value = null
        break

      case 'error':
        console.error('[Chat] Error event:', event)
        reply.isDone = true
        isStreaming.value = false
        messages.value.push({
          id: reply.replyId,
          role: 'assistant',
          content: reply.summaryText || `Error: ${(event as any).message ?? 'Unknown error'}`,
          timestamp: Date.now(),
          reply: { ...reply },
        })
        currentReply.value = null
        break
    }
  }

  function _upsertStepCard(reply: ReplyState, card: any) {
    const idx = reply.stepCards.findIndex(c => c.stepId === card.step_id)
    const mapped: StepCard = {
      stepId: card.step_id,
      title: card.title,
      status: card.status,
      sourceType: card.source_type,
      cardType: card.card_type,
      duration: card.duration ?? null,
      planStepIndex: card.plan_step_index ?? null,
      agentId: card.agent_id ?? 'main',
      input: card.input ?? null,
      output: card.output ?? null,
      absorbedCalls: card.absorbed_calls ?? [],
    }
    if (idx >= 0) {
      reply.stepCards[idx] = mapped
    } else {
      reply.stepCards.push(mapped)
    }
  }

  function _handleTimer(reply: ReplyState, event: any) {
    const phase = event.phase as 'ttft' | 'total'
    if (reply.timer[phase]) {
      reply.timer[phase].state = event.state
      if (event.value != null) {
        reply.timer[phase].value = event.value
      }
    }
  }

  function addUserMessage(content: string) {
    messages.value.push({
      id: `user_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now(),
    })
    startNewReply(`reply_${Date.now()}`)
  }

  return { messages, currentReply, isStreaming, dispatchEvent, addUserMessage, startNewReply }
})
