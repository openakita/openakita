<template>
  <div class="app-layout">
    <LeftSidebar class="sidebar" />
    <ChatArea class="main" />
    <RightPanel v-if="uiStore.rightPanelOpen" class="detail" />
  </div>
</template>

<script setup lang="ts">
import { useUIStore } from '@/stores/ui'
import LeftSidebar from '@/components/layout/LeftSidebar.vue'
import ChatArea from '@/components/layout/ChatArea.vue'
import RightPanel from '@/components/layout/RightPanel.vue'

const uiStore = useUIStore()
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}
.sidebar {
  width: 260px;
  flex-shrink: 0;
}
.main {
  flex: 1;
  min-width: 0;
}
.detail {
  width: 400px;
  flex-shrink: 0;
}
</style>
