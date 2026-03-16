// apps/seecrab/src/stores/session.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { httpClient } from '@/api/http-client'
import type { Session } from '@/types'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref<Session[]>([])
  const activeSessionId = ref<string | null>(null)

  async function loadSessions() {
    try {
      const { sessions: list } = await httpClient.listSessions()
      sessions.value = list.map((s: any) => ({
        id: s.id,
        title: s.title || '',
        lastMessage: s.last_message || '',
        updatedAt: s.updated_at || Date.now(),
        messageCount: s.message_count || 0,
      }))
      // Sort by most recent first (defensive — backend also sorts)
      sessions.value.sort((a, b) => b.updatedAt - a.updatedAt)
      // Auto-select the most recent session if none active
      if (!activeSessionId.value && sessions.value.length > 0) {
        activeSessionId.value = sessions.value[0].id
      }
    } catch (e) {
      console.warn('[Session] Failed to load sessions:', e)
    }
  }

  async function createSession() {
    const { session_id } = await httpClient.createSession()
    sessions.value.unshift({
      id: session_id,
      title: '',
      lastMessage: '',
      updatedAt: Date.now(),
      messageCount: 0,
    })
    activeSessionId.value = session_id
    return session_id
  }

  async function deleteSession(id: string) {
    try {
      await httpClient.deleteSession(id)
    } catch {
      // Session may already be gone on backend, still remove locally
    }
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (activeSessionId.value === id) {
      activeSessionId.value = sessions.value.length > 0 ? sessions.value[0].id : null
    }
  }

  function selectSession(id: string) {
    activeSessionId.value = id
  }

  function updateSessionTitle(id: string, title: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) {
      session.title = title
    }
    // Persist to backend (fire-and-forget, local update is source of truth for UI)
    httpClient.updateSession(id, { title }).catch(e => {
      console.warn('[Session] Failed to persist title:', e)
    })
  }

  function incrementStepCount(id: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) {
      session.messageCount += 1
      session.updatedAt = Date.now()
    }
  }

  function updateLastMessage(id: string, lastMessage: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) {
      session.lastMessage = lastMessage.length > 60 ? lastMessage.substring(0, 60) + '...' : lastMessage
      session.updatedAt = Date.now()
    }
  }

  return {
    sessions, activeSessionId,
    loadSessions, createSession, deleteSession, selectSession,
    updateSessionTitle, incrementStepCount, updateLastMessage,
  }
})
