<template>
  <div class="app-layout">
    <div class="bg-mesh" aria-hidden="true"></div>
    <LeftSidebar class="sidebar" />
    <ChatArea class="main" />
    <transition name="slide-panel">
      <RightPanel v-if="uiStore.rightPanelOpen" class="detail" />
    </transition>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/session'
import { useChatStore } from '@/stores/chat'
import LeftSidebar from '@/components/layout/LeftSidebar.vue'
import ChatArea from '@/components/layout/ChatArea.vue'
import RightPanel from '@/components/layout/RightPanel.vue'

const uiStore = useUIStore()
const sessionStore = useSessionStore()
const chatStore = useChatStore()

onMounted(async () => {
  await sessionStore.loadSessions()
  // Load messages for auto-selected session
  if (sessionStore.activeSessionId) {
    await chatStore.loadSessionMessages(sessionStore.activeSessionId)
  }
})
</script>

<style scoped>
.app-layout {
  display: flex;
  width: 100%;
  height: 100vh;
  overflow: hidden;
  position: relative;
}

.bg-mesh {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    radial-gradient(ellipse 600px 400px at 10% 90%, rgba(56, 189, 204, 0.03), transparent),
    radial-gradient(ellipse 500px 500px at 85% 20%, rgba(240, 128, 108, 0.02), transparent),
    radial-gradient(ellipse 800px 600px at 50% 50%, rgba(56, 189, 204, 0.015), transparent);
}

.sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}
.main {
  flex: 1;
  min-width: 0;
  position: relative;
  z-index: 1;
}

/* Right panel as a proper third column */
.detail {
  width: var(--right-panel-width);
  flex-shrink: 0;
  position: relative;
  z-index: 2;
  overflow: hidden;
}

/* Panel slide transition */
.slide-panel-enter-active {
  transition: width 0.3s var(--ease-out), opacity 0.3s var(--ease-out);
}
.slide-panel-leave-active {
  transition: width 0.2s ease-in, opacity 0.2s ease-in;
}
.slide-panel-enter-from,
.slide-panel-leave-to {
  width: 0;
  opacity: 0;
}
</style>
