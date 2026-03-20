<!-- apps/seecrab/src/components/layout/RightPanel.vue -->
<template>
  <aside class="right-panel">
    <div class="panel-header">
      <div class="panel-title-area">
        <span class="material-symbols-rounded panel-type-icon">{{ stepTypeIcon }}</span>
        <span class="panel-title">{{ stepTitle }}</span>
      </div>
      <button class="close-btn" @click="uiStore.closeRightPanel()" title="关闭">
        <span class="material-symbols-rounded">close</span>
      </button>
    </div>
    <StepDetail v-if="uiStore.rightPanelMode === 'step-detail' && uiStore.selectedStepId" :step-id="uiStore.selectedStepId" />
    <SubtaskOutputPanel v-else-if="uiStore.rightPanelMode === 'subtask-output'" />
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useChatStore } from '@/stores/chat'
import { useBestPracticeStore } from '@/stores/bestpractice'
import StepDetail from '@/components/detail/StepDetail.vue'
import SubtaskOutputPanel from '@/components/panel/SubtaskOutputPanel.vue'

const uiStore = useUIStore()
const chatStore = useChatStore()
const bpStore = useBestPracticeStore()

function findStep() {
  if (!uiStore.selectedStepId) return null
  if (chatStore.currentReply) {
    const found = chatStore.currentReply.stepCards.find(
      c => c.stepId === uiStore.selectedStepId
    )
    if (found) return found
  }
  for (const msg of chatStore.messages) {
    if (msg.reply) {
      const found = msg.reply.stepCards.find(c => c.stepId === uiStore.selectedStepId)
      if (found) return found
    }
  }
  return null
}

const stepTitle = computed(() => {
  if (uiStore.rightPanelMode === 'subtask-output') {
    const inst = uiStore.selectedBPInstanceId ? bpStore.instances.get(uiStore.selectedBPInstanceId) : null
    const st = inst?.subtasks.find(s => s.id === uiStore.selectedSubtaskId)
    return st?.name ?? '子任务输出'
  }
  return findStep()?.title ?? '步骤详情'
})

const stepTypeIcon = computed(() => {
  if (uiStore.rightPanelMode === 'subtask-output') {
    return 'checklist'
  }
  const step = findStep()
  if (!step) return 'info'
  const map: Record<string, string> = {
    search: 'search', code: 'code', file: 'description',
    analysis: 'analytics', browser: 'language', default: 'build',
  }
  return map[step.cardType] ?? 'info'
})
</script>

<style scoped>
.right-panel {
  background: var(--bg-mid);
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
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  min-height: 48px;
}
.panel-title-area {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.panel-type-icon {
  font-size: 18px;
  color: var(--accent);
  flex-shrink: 0;
}
.panel-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-bright);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.close-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 5px;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.close-btn .material-symbols-rounded { font-size: 20px; }
.close-btn:hover {
  background: var(--bg-hover);
  color: var(--text-bright);
}
</style>
