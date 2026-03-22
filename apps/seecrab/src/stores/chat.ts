// apps/seecrab/src/stores/chat.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useSessionStore } from './session'
import { httpClient } from '@/api/http-client'
import { useBestPracticeStore } from './bestpractice'
import type { Message, ReplyState, StepCard, PlanStep, SSEEvent, BPInstanceState } from '@/types'

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
      agentThinking: {},
      timer: {
        ttft: { state: 'idle', value: null },
        total: { state: 'idle', value: null },
      },
      askUser: null,
      bpProgress: null,
      bpSubtaskOutput: null,
      bpInstanceCreated: null,
      bpAskUser: null,
      bpOffer: null,
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
      case 'thinking': {
        const aid = (event as any).agent_id
        if (aid && aid !== 'main') {
          if (!reply.agentThinking[aid]) {
            reply.agentThinking[aid] = { content: '', done: false }
          }
          reply.agentThinking[aid].content += (event as any).content ?? ''
        } else {
          reply.thinking += (event as any).content ?? ''
        }
        break
      }

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

      case 'bp_instance_created': {
        const bpStore = useBestPracticeStore()
        bpStore.handleInstanceCreated(event)
        reply.bpInstanceCreated = {
          instanceId: (event as any).instance_id,
          bpId: (event as any).bp_id,
          bpName: (event as any).bp_name,
          runMode: (event as any).run_mode,
          subtasks: (event as any).subtasks,
        }
        break
      }

      case 'bp_subtask_start': {
        const bpStore = useBestPracticeStore()
        const e = event as any
        bpStore.handleSubtaskStart(e.instance_id, e.subtask_id)
        break
      }

      case 'bp_subtask_complete': {
        const bpStore = useBestPracticeStore()
        const e = event as any
        bpStore.updateSubtaskOutput(e.instance_id, e.subtask_id, e.output, {
          summary: e.summary,
          outputSchema: e.output_schema,
          subtaskName: e.subtask_name,
        })
        reply.bpSubtaskOutput = {
          subtaskId: e.subtask_id,
          output: e.output,
          summary: e.summary,
        }
        break
      }

      case 'bp_waiting_next': {
        // No extra action — 'done' event will set isDone=true, enabling buttons
        break
      }

      case 'bp_offer': {
        const e = event as any
        reply.bpOffer = {
          bpId: e.bp_id,
          bpName: e.bp_name,
          subtasks: e.subtasks ?? [],
          defaultRunMode: e.default_run_mode ?? 'manual',
        }
        break
      }

      case 'bp_ask_user': {
        const e = event as any
        reply.bpAskUser = {
          instanceId: e.instance_id,
          subtaskId: e.subtask_id,
          subtaskName: e.subtask_name,
          missingFields: e.missing_fields,
          inputSchema: e.input_schema,
        }
        break
      }

      case 'bp_complete': {
        const bpStore = useBestPracticeStore()
        const e = event as any
        bpStore.handleComplete(e.instance_id)
        break
      }

      case 'bp_error': {
        const e = event as any
        reply.errorMessage = e.error || 'BP 执行出错'
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
        for (const at of Object.values(reply.agentThinking)) {
          at.done = true
        }
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
      delegateAgentId: card.delegate_agent_id || undefined,
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
          agentThinking: _mapAgentThinking(rs?.agent_thinking),
          timer: {
            ttft: { state: 'done' as const, value: rs?.timer?.ttft ?? null },
            total: { state: 'done' as const, value: rs?.timer?.total ?? null },
          },
          askUser: null,
          bpProgress: _mapBPProgress(rs?.bp_progress),
          bpSubtaskOutput: rs?.bp_subtask_output ? {
            subtaskId: rs.bp_subtask_output.subtask_id,
            output: rs.bp_subtask_output.output,
            summary: rs.bp_subtask_output.summary,
          } : null,
          bpInstanceCreated: rs?.bp_instance_created ? {
            instanceId: rs.bp_instance_created.instance_id,
            bpId: rs.bp_instance_created.bp_id,
            bpName: rs.bp_instance_created.bp_name,
            runMode: rs.bp_instance_created.run_mode,
            subtasks: rs.bp_instance_created.subtasks ?? [],
          } : null,
          bpAskUser: null,
          bpOffer: null,
          isDone: true,
        }
      }
      return msg
    })
  }

  function _mapAgentThinking(raw: Record<string, any> | undefined): Record<string, { content: string; done: boolean }> {
    if (!raw) return {}
    const result: Record<string, { content: string; done: boolean }> = {}
    for (const [key, val] of Object.entries(raw)) {
      result[key] = { content: val?.content ?? '', done: true }
    }
    return result
  }

  function _mapBPProgress(raw: any): BPInstanceState | null {
    if (!raw) return null
    return {
      instanceId: raw.instance_id ?? '',
      bpId: raw.bp_id ?? '',
      bpName: raw.bp_name ?? '',
      status: raw.status ?? 'active',
      runMode: raw.run_mode ?? 'manual',
      subtasks: (raw.subtasks ?? []).map((s: any) => ({
        id: s.id,
        name: s.name,
        status: raw.statuses?.[s.id] ?? 'pending',
      })),
      currentSubtaskIndex: raw.current_subtask_index ?? 0,
    }
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
      delegateAgentId: raw.delegate_agent_id || undefined,
      input: raw.input ?? null,
      output: raw.output ?? null,
      absorbedCalls: raw.absorbed_calls ?? [],
    }
  }

  async function loadSessionMessages(sessionId: string) {
    try {
      const data = await httpClient.getSession(sessionId)
      messages.value = _mapHistoryMessages(data.messages || [])

      // Restore BP store from persisted messages
      const bpStore = useBestPracticeStore()
      bpStore.clear()
      for (const msg of messages.value) {
        if (msg.reply?.bpProgress) {
          const bp = msg.reply.bpProgress
          bpStore.updateFromProgress({
            instance_id: bp.instanceId,
            bp_name: bp.bpName,
            statuses: Object.fromEntries(
              bp.subtasks.map(s => [s.id, s.status])
            ),
            subtasks: bp.subtasks.map(s => ({ id: s.id, name: s.name })),
            current_subtask_index: bp.currentSubtaskIndex,
            run_mode: bp.runMode,
            status: bp.status,
          })
        }
        if (msg.reply?.bpSubtaskOutput && msg.reply.bpProgress) {
          bpStore.updateSubtaskOutput(
            msg.reply.bpProgress.instanceId,
            msg.reply.bpSubtaskOutput.subtaskId,
            msg.reply.bpSubtaskOutput.output,
            { summary: msg.reply.bpSubtaskOutput.summary },
          )
        }
      }
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
