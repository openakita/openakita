<script setup lang="ts">
import { computed } from 'vue'
import { useBestPracticeStore } from '@/stores/bestpractice'
import { useUIStore } from '@/stores/ui'

const bpStore = useBestPracticeStore()
const uiStore = useUIStore()

const output = computed(() => {
  if (!uiStore.selectedBPInstanceId || !uiStore.selectedSubtaskId) return null
  const inst = bpStore.instances.get(uiStore.selectedBPInstanceId)
  if (!inst) return null
  const st = inst.subtasks.find(s => s.id === uiStore.selectedSubtaskId)
  return st?.output ?? null
})

const subtaskName = computed(() => {
  if (!uiStore.selectedBPInstanceId || !uiStore.selectedSubtaskId) return ''
  const inst = bpStore.instances.get(uiStore.selectedBPInstanceId)
  const st = inst?.subtasks.find(s => s.id === uiStore.selectedSubtaskId)
  return st?.name ?? uiStore.selectedSubtaskId
})
</script>

<template>
  <div class="subtask-output-panel">
    <div class="panel-header">
      <h3>{{ subtaskName }}</h3>
      <button class="close-btn" @click="uiStore.closeRightPanel()">×</button>
    </div>
    <div v-if="output" class="output-content">
      <div v-for="(value, key) in output" :key="String(key)" class="output-field">
        <label class="field-label">{{ key }}</label>
        <pre class="field-value">{{ typeof value === 'object' ? JSON.stringify(value, null, 2) : value }}</pre>
      </div>
    </div>
    <div v-else class="empty-state">暂无输出数据</div>
  </div>
</template>

<style scoped>
.subtask-output-panel { padding: 16px; height: 100%; overflow-y: auto; }
.panel-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 16px; padding-bottom: 8px;
  border-bottom: 1px solid var(--border-color, #2a2a4a);
}
.panel-header h3 { margin: 0; font-size: 15px; color: var(--text-primary, #e0e0e0); }
.close-btn {
  background: none; border: none; color: var(--text-secondary, #888);
  font-size: 20px; cursor: pointer; padding: 0 4px;
}
.output-field { margin-bottom: 12px; }
.field-label {
  display: block; font-size: 12px; color: var(--text-secondary, #888);
  margin-bottom: 4px; text-transform: uppercase;
}
.field-value {
  background: var(--bg-tertiary, #2a2a4a); border-radius: 4px;
  padding: 8px 12px; font-size: 13px; color: var(--text-primary, #e0e0e0);
  white-space: pre-wrap; word-break: break-word; margin: 0;
}
.empty-state { color: var(--text-secondary, #888); text-align: center; padding: 40px 0; }
</style>
