// apps/seecrab/src/stores/session.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { httpClient } from '@/api/http-client'
import type { Session } from '@/types'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref<Session[]>([])
  const activeSessionId = ref<string | null>(null)

  async function loadSessions() {
    const { sessions: list } = await httpClient.listSessions()
    sessions.value = list
  }

  async function createSession() {
    const { session_id } = await httpClient.createSession()
    activeSessionId.value = session_id
    return session_id
  }

  function selectSession(id: string) {
    activeSessionId.value = id
  }

  return { sessions, activeSessionId, loadSessions, createSession, selectSession }
})
