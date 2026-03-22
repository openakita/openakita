<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{
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

const { render } = useMarkdown()
const renderedSummary = computed(() => props.summary ? render(props.summary) : '')
</script>

<template>
  <div class="subtask-complete-block">
    <div class="complete-label">
      <span class="material-symbols-rounded done-icon">check_circle</span>
      <span class="label-text">子任务 {{ subtaskIndex + 1 }}「{{ subtaskName }}」已完成</span>
    </div>
    <div v-if="summary" class="summary-content" v-html="renderedSummary"></div>
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
  padding: 8px 0;
  margin: 10px 0;
  animation: fadeIn 0.35s var(--ease-out) both;
}

.complete-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--text-muted, #556174);
}
.done-icon {
  font-size: 15px;
  color: var(--success, #4caf50);
}
.label-text {
  font-weight: 500;
}

/* ── Markdown summary (reuse SummaryOutput styles) ── */
.summary-content {
  font-size: 14px;
  line-height: 1.75;
  color: var(--text-primary, #c4cdd9);
  margin-bottom: 14px;
}
.summary-content :deep(p) { margin-bottom: 12px; }
.summary-content :deep(p:last-child) { margin-bottom: 0; }
.summary-content :deep(strong) { color: var(--text-bright, #e8edf5); font-weight: 600; }
.summary-content :deep(em) { color: var(--text-secondary, #8494a7); }
.summary-content :deep(a) {
  color: var(--accent, #4a6cf7);
  text-decoration: none;
  border-bottom: 1px solid var(--accent-dim, rgba(74, 108, 247, 0.2));
  transition: border-color 0.15s;
}
.summary-content :deep(a:hover) { border-color: var(--accent); }
.summary-content :deep(pre) {
  background: var(--bg-deep, #0d1117);
  border: 1px solid var(--border, rgba(56, 189, 204, 0.08));
  padding: 14px 16px;
  border-radius: var(--radius-md, 10px);
  overflow-x: auto;
  font-size: 12px;
  margin: 10px 0;
  line-height: 1.6;
}
.summary-content :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
}
.summary-content :deep(:not(pre) > code) {
  background: var(--bg-elevated, #1e2536);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--accent, #4a6cf7);
}
.summary-content :deep(ul), .summary-content :deep(ol) {
  padding-left: 20px;
  margin-bottom: 12px;
}
.summary-content :deep(li) { margin-bottom: 4px; }
.summary-content :deep(blockquote) {
  border-left: 3px solid var(--accent, #4a6cf7);
  padding-left: 14px;
  color: var(--text-secondary, #8494a7);
  margin: 10px 0;
}
.summary-content :deep(h1), .summary-content :deep(h2), .summary-content :deep(h3) {
  color: var(--text-bright, #e8edf5);
  margin: 16px 0 8px;
  font-weight: 600;
}
.summary-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--border, rgba(56, 189, 204, 0.08));
  margin: 16px 0;
}

/* ── Action buttons ── */
.complete-actions {
  display: flex;
  gap: 10px;
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
