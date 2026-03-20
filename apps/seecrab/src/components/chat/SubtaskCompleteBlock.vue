<script setup lang="ts">
defineProps<{
  subtaskName: string
  subtaskId: string
  instanceId: string
  isLastSubtask: boolean
  summary?: string
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
      <span class="check-icon">✓</span>
      <span>「{{ subtaskName }}」已完成</span>
    </div>
    <div v-if="summary" class="summary-text">{{ summary }}</div>
    <div class="complete-actions">
      <button class="action-btn" @click="emit('view-output', subtaskId)">
        查看结果
      </button>
      <button
        v-if="!isLastSubtask"
        class="action-btn primary"
        @click="emit('continue')"
      >
        进入下一步
      </button>
      <button class="action-btn" @click="emit('edit', subtaskId)">
        修改结果
      </button>
    </div>
  </div>
</template>

<style scoped>
.subtask-complete-block {
  background: var(--bg-secondary, #1a1a2e);
  border: 1px solid var(--success-color, #4caf50);
  border-radius: 8px;
  padding: 12px 16px;
  margin: 8px 0;
}
.complete-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  color: var(--success-color, #4caf50);
  font-weight: 500;
}
.check-icon { font-size: 16px; }
.summary-text {
  font-size: 13px;
  color: var(--text-secondary, #888);
  margin-bottom: 10px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
.complete-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.action-btn {
  padding: 6px 14px;
  font-size: 13px;
  border: 1px solid var(--border-color, #2a2a4a);
  background: transparent;
  color: var(--text-primary, #e0e0e0);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.action-btn:hover { background: var(--bg-tertiary, #2a2a4a); }
.action-btn.primary {
  background: var(--accent-color, #4a6cf7);
  border-color: var(--accent-color, #4a6cf7);
  color: #fff;
}
.action-btn.primary:hover { opacity: 0.9; }
</style>
