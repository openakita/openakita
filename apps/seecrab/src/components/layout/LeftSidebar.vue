<!-- apps/seecrab/src/components/layout/LeftSidebar.vue -->
<template>
  <aside class="left-sidebar">
    <div class="sidebar-header">
      <button class="new-chat-btn" @click="onNewChat">
        <span class="material-symbols-rounded">add</span> 新对话
      </button>
    </div>
    <div class="session-list scrollbar-thin">
      <div
        v-for="s in sessionStore.sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === sessionStore.activeSessionId }"
        @click="sessionStore.selectSession(s.id)"
      >
        <span class="session-title">{{ s.title || '新对话' }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'
import { useChatStore } from '@/stores/chat'

const sessionStore = useSessionStore()
const chatStore = useChatStore()

async function onNewChat() {
  const id = await sessionStore.createSession()
  chatStore.messages = []
}
</script>

<style scoped>
.left-sidebar {
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.sidebar-header { padding: 16px; }
.new-chat-btn {
  width: 100%;
  padding: 10px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  font-size: 14px;
}
.new-chat-btn:hover { background: var(--accent-hover); }
.session-list { flex: 1; overflow-y: auto; padding: 0 8px; }
.session-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 13px;
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-item:hover { background: var(--bg-hover); }
.session-item.active { background: var(--bg-tertiary); color: var(--text-primary); }
</style>
