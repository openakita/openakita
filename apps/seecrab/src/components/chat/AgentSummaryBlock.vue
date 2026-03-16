<template>
  <div v-if="summary" class="agent-summary-block">
    <div class="summary-header">
      <span class="material-symbols-rounded icon">smart_toy</span>
      <span class="label">{{ agentId }} 总结</span>
      <button v-if="isLong" class="toggle" @click="expanded = !expanded">
        {{ expanded ? '收起' : '展开' }}
      </button>
    </div>
    <div class="summary-content" v-html="renderedText"></div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{ agentId: string; summary: string }>()
const { render } = useMarkdown()
const expanded = ref(false)
const MAX_LEN = 500

const isLong = computed(() => props.summary.length > MAX_LEN)
const displayText = computed(() =>
  isLong.value && !expanded.value
    ? props.summary.slice(0, MAX_LEN) + '...'
    : props.summary
)
const renderedText = computed(() => render(displayText.value))
</script>

<style scoped>
.agent-summary-block {
  margin-left: 24px;
  margin-bottom: 6px;
  padding: 8px 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-left: 2px solid var(--accent-dim);
  border-radius: var(--radius-md);
  font-size: 13px;
  animation: fadeIn 0.3s var(--ease-out) both;
}
.summary-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  color: var(--text-ghost);
  font-size: 11px;
}
.summary-header .icon {
  font-size: 14px;
}
.summary-header .label {
  flex: 1;
}
.toggle {
  background: none;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 11px;
  padding: 0;
}
.toggle:hover {
  text-decoration: underline;
}
.summary-content {
  color: var(--text-secondary);
  line-height: 1.6;
}
.summary-content :deep(p) { margin-bottom: 6px; }
.summary-content :deep(p:last-child) { margin-bottom: 0; }
.summary-content :deep(strong) { color: var(--text-primary); }
.summary-content :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
  background: var(--bg-elevated);
  padding: 1px 4px;
  border-radius: 3px;
}
</style>
