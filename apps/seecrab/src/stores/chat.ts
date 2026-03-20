// apps/seecrab/src/stores/chat.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useSessionStore } from './session'
import { httpClient } from '@/api/http-client'
import { useBestPracticeStore } from './bestpractice'
import type { Message, ReplyState, StepCard, PlanStep, SSEEvent } from '@/types'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const currentReply = ref<ReplyState | null>(null)
  const isStreaming = ref(false)

  function startNewReply(replyId: string) {
    currentReply.value = {
      replyId,
      agentId: 'main',
      agentName: 'SeeAgent',
      thinking: '',
      thinkingDone: false,
      planChecklist: null,
      stepCards: [],
      summaryText: '',
      agentSummaries: {},
      timer: {
        ttft: { state: 'idle', value: null },
        total: { state: 'idle', value: null },
      },
      askUser: null,
      bpProgress: null,
      bpSubtaskOutput: null,
      bpTrigger: null,
      isDone: false,
    }
    isStreaming.value = true
  }

  function dispatchEvent(event: SSEEvent) {
    // session_title can arrive independently of a reply
    if (event.type === 'session_title') {
      const sessionStore = useSessionStore()
      const sid = (event as any).session_id ?? sessionStore.activeSessionId
      const title = (event as any).title ?? ''
      if (sid && title) {
        sessionStore.updateSessionTitle(sid, title)
      }
      return
    }

    if (!currentReply.value) {
      startNewReply(`reply_${Date.now()}`)
    }
    const reply = currentReply.value!

    switch (event.type) {
      case 'thinking':
        reply.thinking += (event as any).content ?? ''
        break

      case 'step_card': {
        _upsertStepCard(reply, event as any)
        // Track step count in session
        const sessionStore = useSessionStore()
        if (sessionStore.activeSessionId && (event as any).status === 'completed') {
          sessionStore.incrementStepCount(sessionStore.activeSessionId)
        }
        break
      }

      case 'ai_text': {
        const aid = (event as any).agent_id ?? reply.agentId
        if (aid && aid !== 'main') {
          reply.agentSummaries[aid] = (reply.agentSummaries[aid] ?? '') + ((event as any).content ?? '')
        } else {
          reply.summaryText += (event as any).content ?? ''
        }
        break
      }

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

      case 'bp_progress': {
        const bpStore = useBestPracticeStore()
        bpStore.updateFromProgress(event as any)
        if (reply) {
          reply.bpProgress = bpStore.activeInstance
        }
        break
      }

      case 'bp_subtask_output': {
        const bpStore = useBestPracticeStore()
        const e = event as any
        bpStore.updateSubtaskOutput(e.instance_id, e.subtask_id, e.output, {
          summary: e.summary,
          outputSchema: e.output_schema,
          subtaskName: e.subtask_name,
        })
        if (reply) {
          reply.bpSubtaskOutput = {
            subtaskId: e.subtask_id,
            output: e.output,
            summary: e.summary,
          }
        }
        break
      }

      case 'bp_stale': {
        const bpStore = useBestPracticeStore()
        const e = event as any
        bpStore.markStale(e.instance_id, e.stale_subtask_ids)
        break
      }

      case 'agent_header':
        reply.agentId = (event as any).agent_id ?? 'main'
        reply.agentName = (event as any).agent_name ?? 'Agent'
        break

      case 'done':
        console.log('[BP-DEBUG][Chat] DONE event — pushing reply to messages. replyId:', reply.replyId,
          'stepCards:', reply.stepCards.length, 'bpProgress:', !!reply.bpProgress,
          'bpSubtaskOutput:', !!reply.bpSubtaskOutput, 'summaryText:', reply.summaryText?.slice(0, 100))
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
        // Update session lastMessage with assistant summary
        if (reply.summaryText) {
          const sessionStore = useSessionStore()
          if (sessionStore.activeSessionId) {
            sessionStore.updateLastMessage(sessionStore.activeSessionId, reply.summaryText)
          }
        }
        // Generate LLM title after first round completes (1 user + 1 assistant = 2)
        if (messages.value.length === 2) {
          const sessionStore = useSessionStore()
          const sid = sessionStore.activeSessionId
          const userMsg = messages.value[0]?.content ?? ''
          const aiReply = reply.summaryText ?? ''
          if (sid && userMsg) {
            httpClient.generateTitle(userMsg, aiReply).then(({ title }) => {
              if (title && sessionStore.activeSessionId === sid) {
                sessionStore.updateSessionTitle(sid, title)
              }
            }).catch(() => {})
          }
        }
        currentReply.value = null
        break

      case 'error':
        console.error('[BP-DEBUG][Chat] ERROR event:', event)
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
      agentId: card.agent_id || 'main',
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

  function _parseTimestamp(ts: any): number {
    if (!ts) return Date.now()
    if (typeof ts === 'number') return ts
    const parsed = new Date(ts).getTime()
    return isNaN(parsed) ? Date.now() : parsed
  }

  function _mapHistoryMessages(rawMessages: any[]): Message[] {
    return rawMessages.map((m: any, i: number) => {
      const ts = _parseTimestamp(m.timestamp)
      const msg: Message = {
        id: `${m.role}_${ts}_${i}`,
        role: m.role,
        content: m.content || '',
        timestamp: ts,
      }
      if (m.role === 'assistant' && m.content) {
        const rs = m.reply_state
        msg.reply = {
          replyId: msg.id,
          agentId: 'main',
          agentName: 'SeeAgent',
          thinking: rs?.thinking ?? '',
          thinkingDone: true,
          planChecklist: rs?.plan_checklist ?? null,
          stepCards: (rs?.step_cards ?? []).map(_mapStepCard),
          summaryText: m.content,
          agentSummaries: rs?.agent_summaries ?? {},
          timer: {
            ttft: { state: 'done' as const, value: rs?.timer?.ttft ?? null },
            total: { state: 'done' as const, value: rs?.timer?.total ?? null },
          },
          askUser: null,
          bpProgress: null,
          bpSubtaskOutput: null,
          bpTrigger: null,
          isDone: true,
        }
      }
      return msg
    })
  }

  function _mapStepCard(raw: any): StepCard {
    return {
      stepId: raw.step_id,
      title: raw.title,
      status: raw.status,
      sourceType: raw.source_type,
      cardType: raw.card_type,
      duration: raw.duration ?? null,
      planStepIndex: raw.plan_step_index ?? null,
      agentId: raw.agent_id ?? 'main',
      input: raw.input ?? null,
      output: raw.output ?? null,
      absorbedCalls: raw.absorbed_calls ?? [],
    }
  }

  async function loadSessionMessages(sessionId: string) {
    try {
      const data = await httpClient.getSession(sessionId)
      messages.value = _mapHistoryMessages(data.messages || [])
    } catch {
      messages.value = []
    }
  }

  function addUserMessage(content: string) {
    console.log('[BP-DEBUG][Chat] addUserMessage:', content, 'existing msgs:', messages.value.length, 'isStreaming:', isStreaming.value)
    // On first user message, set session title from message content
    const sessionStore = useSessionStore()
    const isFirstMessage = messages.value.length === 0
    messages.value.push({
      id: `user_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now(),
    })
    if (sessionStore.activeSessionId) {
      sessionStore.updateLastMessage(sessionStore.activeSessionId, content)
      if (isFirstMessage) {
        const title = content.length > 30 ? content.substring(0, 30) + '...' : content
        sessionStore.updateSessionTitle(sessionStore.activeSessionId, title)
      }
    }
    startNewReply(`reply_${Date.now()}`)
  }

  function cancelCurrentReply() {
    if (!currentReply.value) {
      isStreaming.value = false
      return
    }
    const reply = currentReply.value
    reply.isDone = true
    isStreaming.value = false
    messages.value.push({
      id: reply.replyId,
      role: 'assistant',
      content: reply.summaryText || '',
      timestamp: Date.now(),
      reply: { ...reply },
    })
    currentReply.value = null
  }

  return { messages, currentReply, isStreaming, dispatchEvent, addUserMessage, startNewReply, loadSessionMessages, cancelCurrentReply }
})
