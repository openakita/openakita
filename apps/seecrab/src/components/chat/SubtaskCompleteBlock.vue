<script setup lang="ts">
defineProps<{
  subtaskName: string
  subtaskId: string
  instanceId: string
  isLastSubtask: boolean
  subtaskIndex: number
  summary?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'view-output', subtaskId: string): void
  (e: 'continue'): void
  (e: 'edit', subtaskId: string): void
}>()
</script>

<template>
  <div class="subtask-complete-block">
    <div class="complete-header">
      <span class="material-symbols-rounded check-icon">task_alt</span>
      <span class="name">子任务 {{ subtaskIndex + 1 }}「{{ subtaskName }}」</span>
      <span class="badge">已完成</span>
    </div>
    <div v-if="summary" class="summary-text">{{ summary }}</div>
    <div class="complete-actions">
      <button class="action-btn secondary" @click="emit('view-output', subtaskId)">
        <span class="material-symbols-rounded">visibility</span>
        查看结果
      </button>
      <button
        v-if="!isLastSubtask"
        class="action-btn primary"
        :disabled="disabled"
        @click="emit('continue')"
      >
        <span class="material-symbols-rounded">arrow_forward</span>
        进入下一步
      </button>
      <button class="action-btn secondary" @click="emit('edit', subtaskId)">
        <span class="material-symbols-rounded">edit</span>
        修改结果
      </button>
    </div>
  </div>
</template>

<style scoped>
.subtask-complete-block {
  background: var(--bg-surface, #171d2a);
  border: 1px solid var(--border-subtle, #252d40);
  border-left: 3px solid var(--success, #4caf50);
  border-radius: var(--radius-md, 10px);
  padding: 14px 16px;
  margin: 10px 0;
  animation: fadeIn 0.35s var(--ease-out) both;
}
.complete-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.check-icon {
  font-size: 18px;
  color: var(--success, #4caf50);
}
.name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-bright, #e8edf5);
}
.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 8px;
  background: var(--success-dim, rgba(61, 214, 140, 0.12));
  color: var(--success, #4caf50);
  font-weight: 500;
}
.summary-text {
  font-size: 13px;
  color: var(--text-secondary, #8494a7);
  line-height: 1.6;
  margin-bottom: 12px;
  padding-left: 26px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
.complete-actions {
  display: flex;
  gap: 10px;
  padding-left: 26px;
  flex-wrap: wrap;
}
.action-btn {
  padding: 8px 18px;
  border-radius: var(--radius-sm, 6px);
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s;
}
.action-btn .material-symbols-rounded {
  font-size: 16px;
}
.action-btn.secondary {
  background: var(--bg-elevated, #1e2536);
  border: 1px solid var(--border, rgba(56, 189, 204, 0.08));
  color: var(--text-primary, #c4cdd9);
}
.action-btn.secondary:hover {
  background: var(--bg-hover, #252d40);
  border-color: var(--text-muted, #556174);
}
.action-btn.primary {
  background: var(--accent-dim, rgba(74, 108, 247, 0.12));
  border: 1px solid var(--accent-color, #4a6cf7);
  color: var(--accent-color, #4a6cf7);
}
.action-btn.primary:hover {
  background: rgba(74, 108, 247, 0.2);
}
.action-btn:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}
</style>
