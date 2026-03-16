// apps/seecrab/src/api/http-client.ts
const BASE = '/api/seecrab'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export const httpClient = {
  listSessions: () => request<{ sessions: any[] }>('/sessions'),
  createSession: () => request<{ session_id: string }>('/sessions', { method: 'POST' }),
  deleteSession: (id: string) => request<{ status: string }>(`/sessions/${id}`, { method: 'DELETE' }),
  getSession: (id: string) => request<{ session_id: string; title: string; messages: any[] }>(`/sessions/${id}`),
  updateSession: (id: string, data: { title?: string }) =>
    request<{ status: string }>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  submitAnswer: (conversationId: string, answer: string) =>
    request('/answer', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, answer }),
    }),
}
