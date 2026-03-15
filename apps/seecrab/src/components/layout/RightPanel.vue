<!-- apps/seecrab/src/components/layout/RightPanel.vue -->
<template>
  <aside class="right-panel">
    <div class="panel-header">
      <span class="panel-title">步骤详情</span>
      <button class="close-btn" @click="uiStore.closeRightPanel()">
        <span class="material-symbols-rounded">close</span>
      </button>
    </div>
    <StepDetail v-if="uiStore.selectedStepId" :step-id="uiStore.selectedStepId" />
  </aside>
</template>

<script setup lang="ts">
import { useUIStore } from '@/stores/ui'
import StepDetail from '@/components/detail/StepDetail.vue'

const uiStore = useUIStore()
</script>

<style scoped>
.right-panel {
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid var(--border);
}
.panel-title { font-weight: 600; font-size: 14px; }
.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
}
.close-btn:hover { background: var(--bg-hover); }
</style>
