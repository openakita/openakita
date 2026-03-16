<!-- apps/seecrab/src/components/layout/LeftSidebar.vue -->
<template>
  <aside class="left-sidebar">
    <div class="sidebar-brand">
      <span class="material-symbols-rounded brand-icon">smart_toy</span>
      <span class="brand-name">OpenCrab</span>
    </div>

    <div class="sidebar-actions">
      <button class="new-chat-btn" @click="onNewChat">
        <span class="material-symbols-rounded btn-icon">add</span>
        <span>New Chat</span>
      </button>
    </div>

    <div class="section-label">RECENT</div>

    <div class="session-list scrollbar-thin">
      <div
        v-for="(s, i) in sessionStore.sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === sessionStore.activeSessionId }"
        :style="{ animationDelay: `${i * 30}ms` }"
        @click="onSelectSession(s.id)"
      >
        <span class="material-symbols-rounded session-icon">{{ sessionIcon(s) }}</span>
        <div class="session-info">
          <span class="session-title">{{ s.title || '新对话' }}</span>
          <span v-if="s.lastMessage" class="session-summary">{{ s.lastMessage }}</span>
          <div class="session-meta">
            <span>{{ s.messageCount || 0 }} 步骤</span>
            <span class="meta-dot">&middot;</span>
            <span>{{ formatTime(s.updatedAt) }}</span>
          </div>
        </div>
        <button
          class="delete-btn"
          title="删除会话"
          @click.stop="onDeleteSession(s.id)"
        >
          <span class="material-symbols-rounded">close</span>
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'
import { useChatStore } from '@/stores/chat'
import { sseClient } from '@/api/sse-client'
import type { Session } from '@/types'

const sessionStore = useSessionStore()
const chatStore = useChatStore()

async function onNewChat() {
  sseClient.abort() // Cancel any in-progress stream
  await sessionStore.createSession()
  chatStore.messages = []
  chatStore.currentReply = null
  chatStore.isStreaming = false
}

async function onSelectSession(id: string) {
  if (id === sessionStore.activeSessionId) return
  sseClient.abort() // Cancel any in-progress stream
  chatStore.currentReply = null
  chatStore.isStreaming = false
  sessionStore.selectSession(id)
  await chatStore.loadSessionMessages(id)
}

async function onDeleteSession(id: string) {
  const wasActive = id === sessionStore.activeSessionId
  if (wasActive) {
    sseClient.abort()
    chatStore.currentReply = null
    chatStore.isStreaming = false
  }
  await sessionStore.deleteSession(id)
  if (wasActive) {
    chatStore.messages = []
    if (sessionStore.activeSessionId) {
      await chatStore.loadSessionMessages(sessionStore.activeSessionId)
    }
  }
}

function sessionIcon(s: Session): string {
  const title = (s.title || '').toLowerCase()
  if (title.includes('搜索') || title.includes('search')) return 'search'
  if (title.includes('代码') || title.includes('code') || title.includes('架构')) return 'code'
  if (title.includes('文档') || title.includes('方案') || title.includes('doc')) return 'description'
  return 'chat_bubble'
}

function formatTime(ts: number): string {
  if (!ts) return '刚刚'
  const diff = Date.now() - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`
  if (diff < 172800_000) return '昨天'
  return `${Math.floor(diff / 86400_000)} 天前`
}
</script>

<style scoped>
.left-sidebar {
  background: var(--bg-mid);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
}

/* ── Brand ── */
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 18px 12px;
}
.brand-icon {
  font-size: 28px;
  color: var(--accent);
  font-variation-settings: 'FILL' 1;
}
.brand-name {
  font-size: 18px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: -0.02em;
}

/* ── New Chat ── */
.sidebar-actions {
  padding: 4px 16px 12px;
}
.new-chat-btn {
  width: 100%;
  padding: 10px 14px;
  background: transparent;
  color: var(--accent);
  border: 1.5px dashed var(--border-accent);
  border-radius: var(--radius-md);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  transition: all 0.2s var(--ease-out);
}
.new-chat-btn:hover {
  background: var(--accent-dim);
  border-style: solid;
  border-color: var(--accent);
}
.btn-icon { font-size: 18px; }

/* ── Section ── */
.section-label {
  padding: 6px 20px 8px;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-ghost);
  letter-spacing: 1.5px;
  text-transform: uppercase;
}

/* ── Sessions ── */
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.session-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 13px;
  margin-bottom: 2px;
  transition: all 0.15s var(--ease-out);
  animation: fadeIn 0.3s var(--ease-out) both;
  position: relative;
}
.session-item:hover {
  background: var(--bg-hover);
}
.session-item.active {
  background: var(--bg-elevated);
  color: var(--text-bright);
}
.session-icon {
  font-size: 18px;
  color: var(--text-ghost);
  margin-top: 1px;
  flex-shrink: 0;
  transition: color 0.15s;
}
.session-item.active .session-icon { color: var(--accent); }
.session-info { flex: 1; min-width: 0; }
.session-title {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
  margin-bottom: 2px;
  font-size: 14px;
}
.session-summary {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 4px;
  line-height: 1.4;
}
.session-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-ghost);
}
.meta-dot {
  color: var(--text-ghost);
}

/* ── Delete Button ── */
.delete-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  background: none;
  border: none;
  color: var(--text-ghost);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: all 0.15s var(--ease-out);
}
.delete-btn .material-symbols-rounded {
  font-size: 16px;
}
.session-item:hover .delete-btn {
  opacity: 1;
}
.delete-btn:hover {
  color: var(--text-bright);
  background: rgba(255, 255, 255, 0.1);
}
</style>
