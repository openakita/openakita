<script setup lang="ts">
import { computed } from 'vue'
import type { BPInstanceState } from '@/types'

const props = defineProps<{ bp: BPInstanceState }>()
const emit = defineEmits<{
  (e: 'toggle-mode', mode: 'manual' | 'auto'): void
  (e: 'view-output', subtaskId: string): void
}>()

function toggleMode() {
  emit('toggle-mode', props.bp.runMode === 'manual' ? 'auto' : 'manual')
}

function onCardClick() {
  // Open the current or last-done subtask output
  const current = props.bp.subtasks[props.bp.currentSubtaskIndex]
  const lastDone = [...props.bp.subtasks].reverse().find(s => s.status === 'done')
  const target = current ?? lastDone
  if (target) emit('view-output', target.id)
}

function onStepClick(index: number, event: Event) {
  event.stopPropagation()
  const st = props.bp.subtasks[index]
  if (st?.status === 'done') emit('view-output', st.id)
}

function stepClass(status: string) {
  return status === 'done' ? 'done'
    : status === 'current' ? 'current'
    : status === 'failed' ? 'failed'
    : status === 'stale' ? 'stale'
    : 'pending'
}

function connectorClass(index: number) {
  const left = props.bp.subtasks[index]
  const right = props.bp.subtasks[index + 1]
  if (!left || !right) return 'pending'
  if (left.status === 'done' && right.status === 'done') return 'done'
  if (left.status === 'done' && right.status === 'current') return 'active'
  if (left.status === 'done') return 'done'
  return 'pending'
}
</script>

<template>
  <div class="task-card" @click="onCardClick">
    <div class="task-card-header">
      <span class="material-symbols-rounded task-icon">assignment</span>
      <span class="task-name">{{ bp.bpName }}</span>
      <div
        :class="['task-mode-toggle', { auto: bp.runMode === 'auto' }]"
        @click.stop="toggleMode"
      >
        <span class="mode-dot"></span>
        <span class="mode-text">{{ bp.runMode === 'auto' ? '自动' : '手动' }}</span>
      </div>
    </div>
    <div class="task-steps-bar">
      <template v-for="(st, i) in bp.subtasks" :key="st.id">
        <div
          :class="['task-step-item', stepClass(st.status)]"
          @click="onStepClick(i, $event)"
        >
          <span class="step-dot"></span>
          <span class="step-label">{{ i + 1 }}. {{ st.name || `步骤 ${i + 1}` }}</span>
        </div>
        <div
          v-if="i < bp.subtasks.length - 1"
          :class="['task-step-connector', connectorClass(i)]"
        ></div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.task-card {
  padding: 14px 16px;
  background: var(--bg-surface, #1a1a2e);
  border: 1px solid var(--border-subtle, #2a2a4a);
  border-radius: 8px;
  margin: 8px 0;
  cursor: pointer;
  transition: all 0.15s ease;
}
.task-card:hover {
  border-color: var(--accent-color, #4a6cf7);
  background: var(--bg-elevated, #1e1e38);
}

.task-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.task-icon {
  font-size: 18px;
  color: var(--accent-color, #4a6cf7);
}
.task-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #e0e0e0);
  flex: 1;
}

.task-mode-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  background: var(--bg-elevated, #1e1e38);
  border: 1px solid var(--border-color, #2a2a4a);
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.task-mode-toggle:hover {
  border-color: var(--text-secondary, #888);
}
.mode-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-ghost, #555);
  transition: background 0.2s;
}
.task-mode-toggle.auto .mode-dot {
  background: var(--accent-color, #4a6cf7);
}
.mode-text {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary, #888);
  transition: color 0.2s;
}
.task-mode-toggle.auto .mode-text {
  color: var(--accent-color, #4a6cf7);
}

.task-steps-bar {
  display: flex;
  align-items: center;
  margin-top: 12px;
}

.task-step-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  padding: 4px 0;
}
.task-step-item.done { cursor: pointer; }
.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: all 0.3s;
}
.step-label {
  transition: color 0.3s;
  white-space: nowrap;
}

/* Step states */
.task-step-item.pending .step-dot { background: var(--text-ghost, #555); }
.task-step-item.pending .step-label { color: var(--text-ghost, #555); }
.task-step-item.current .step-dot { background: var(--accent-color, #4a6cf7); animation: pulse 1.5s infinite; }
.task-step-item.current .step-label { color: var(--accent-color, #4a6cf7); }
.task-step-item.done .step-dot { background: var(--success-color, #4caf50); }
.task-step-item.done .step-label { color: var(--success-color, #4caf50); }
.task-step-item.failed .step-dot { background: var(--error-color, #f44336); }
.task-step-item.failed .step-label { color: var(--error-color, #f44336); }
.task-step-item.stale .step-dot { background: var(--warning-color, #ff9800); }
.task-step-item.stale .step-label { color: var(--warning-color, #ff9800); }

/* Connector lines */
.task-step-connector {
  width: 24px;
  height: 1px;
  margin: 0 4px;
  transition: background 0.3s;
}
.task-step-connector.done { background: var(--success-color, #4caf50); }
.task-step-connector.active { background: var(--accent-color, #4a6cf7); }
.task-step-connector.pending { background: var(--text-ghost, #555); opacity: 0.3; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
