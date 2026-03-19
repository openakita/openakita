<script setup lang="ts">
import { computed } from 'vue'
import type { BPInstanceState } from '@/types'

const props = defineProps<{ bp: BPInstanceState }>()
const emit = defineEmits<{
  (e: 'toggle-mode', mode: 'manual' | 'auto'): void
  (e: 'view-output', subtaskId: string): void
}>()

const progressPercent = computed(() => {
  const done = props.bp.subtasks.filter(s => s.status === 'done').length
  return Math.round((done / Math.max(props.bp.subtasks.length, 1)) * 100)
})

const statusIcon: Record<string, string> = {
  pending: '○',
  current: '◉',
  done: '✓',
  failed: '✗',
  stale: '⟳',
}

const statusClass: Record<string, string> = {
  pending: 'step-pending',
  current: 'step-current',
  done: 'step-done',
  failed: 'step-failed',
  stale: 'step-stale',
}
</script>

<template>
  <div class="task-progress-card">
    <div class="task-header">
      <span class="task-title">{{ bp.bpName }}</span>
      <div class="mode-toggle">
        <button
          :class="{ active: bp.runMode === 'manual' }"
          @click="emit('toggle-mode', 'manual')"
        >手动</button>
        <button
          :class="{ active: bp.runMode === 'auto' }"
          @click="emit('toggle-mode', 'auto')"
        >自动</button>
      </div>
    </div>
    <div class="progress-bar-container">
      <div class="progress-bar" :style="{ width: progressPercent + '%' }"></div>
    </div>
    <div class="step-list">
      <div
        v-for="(st, i) in bp.subtasks"
        :key="st.id"
        :class="['step-item', statusClass[st.status] || 'step-pending']"
        @click="st.status === 'done' ? emit('view-output', st.id) : undefined"
      >
        <span class="step-icon">{{ statusIcon[st.status] || '○' }}</span>
        <span class="step-name">{{ st.name || `步骤 ${i + 1}` }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.task-progress-card {
  background: var(--bg-secondary, #1a1a2e);
  border: 1px solid var(--border-color, #2a2a4a);
  border-radius: 8px;
  padding: 12px 16px;
  margin: 8px 0;
}
.task-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.task-title {
  font-weight: 600;
  color: var(--text-primary, #e0e0e0);
}
.mode-toggle button {
  padding: 2px 10px;
  font-size: 12px;
  border: 1px solid var(--border-color, #2a2a4a);
  background: transparent;
  color: var(--text-secondary, #888);
  cursor: pointer;
  border-radius: 4px;
  margin-left: 4px;
}
.mode-toggle button.active {
  background: var(--accent-color, #4a6cf7);
  color: #fff;
  border-color: var(--accent-color, #4a6cf7);
}
.progress-bar-container {
  height: 4px;
  background: var(--bg-tertiary, #2a2a4a);
  border-radius: 2px;
  margin-bottom: 10px;
}
.progress-bar {
  height: 100%;
  background: var(--accent-color, #4a6cf7);
  border-radius: 2px;
  transition: width 0.3s ease;
}
.step-list { display: flex; flex-direction: column; gap: 4px; }
.step-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 13px;
  color: var(--text-secondary, #888);
}
.step-item.step-done { cursor: pointer; }
.step-item.step-done:hover { background: var(--bg-tertiary, #2a2a4a); }
.step-icon { width: 16px; text-align: center; }
.step-current { color: var(--accent-color, #4a6cf7); font-weight: 500; }
.step-done { color: var(--success-color, #4caf50); }
.step-failed { color: var(--error-color, #f44336); }
.step-stale { color: var(--warning-color, #ff9800); }
</style>
