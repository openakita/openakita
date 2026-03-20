<script setup lang="ts">
import { computed, ref } from 'vue'
import { useChatStore } from '@/stores/chat'

const props = defineProps<{
  trigger: {
    bpId: string
    bpName: string
    description: string
    subtaskCount: number
    subtasks: { id: string; name: string }[]
  }
}>()

const chatStore = useChatStore()
const chosen = ref(false)

const subtaskChain = computed(() =>
  props.trigger.subtasks.map(s => s.name).join(' → ')
)

function chooseFree() {
  chosen.value = true
  chatStore.addUserMessage('自由模式')
}

function chooseBP() {
  chosen.value = true
  chatStore.addUserMessage('最佳实践模式')
}
</script>

<template>
  <div class="bp-trigger">
    <div class="trigger-msg">
      <span class="material-symbols-rounded trigger-icon">lightbulb</span>
      <p>
        检测到您的需求匹配最佳实践「{{ trigger.bpName }}」，该任务包含
        {{ trigger.subtaskCount }} 个子任务：{{ subtaskChain }}。是否使用最佳实践流程？
      </p>
    </div>
    <div class="trigger-actions">
      <button class="trigger-btn" :disabled="chosen" @click="chooseFree">
        自由模式
      </button>
      <button class="trigger-btn primary" :disabled="chosen" @click="chooseBP">
        最佳实践模式
      </button>
    </div>
  </div>
</template>

<style scoped>
.bp-trigger {
  padding: 14px 16px;
  background: var(--bg-surface, #171d2a);
  border: 1px solid var(--border-accent, rgba(74, 108, 247, 0.2));
  border-radius: var(--radius-md, 10px);
  margin-bottom: 14px;
  animation: fadeIn 0.3s var(--ease-out) both;
}
.trigger-msg {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}
.trigger-icon {
  font-size: 18px;
  color: var(--accent-color, #4a6cf7);
  flex-shrink: 0;
  margin-top: 1px;
}
.trigger-msg p {
  font-size: 14px;
  color: var(--text-bright, #e8edf5);
  line-height: 1.5;
  margin: 0;
}
.trigger-actions {
  display: flex;
  gap: 8px;
  padding-left: 26px;
}
.trigger-btn {
  padding: 7px 16px;
  background: var(--bg-elevated, #1e2536);
  border: 1px solid var(--border, rgba(56, 189, 204, 0.08));
  border-radius: var(--radius-sm, 6px);
  color: var(--text-primary, #c4cdd9);
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  transition: all 0.15s;
}
.trigger-btn:hover {
  background: var(--accent-dim, rgba(74, 108, 247, 0.12));
  border-color: var(--accent-color, #4a6cf7);
  color: var(--accent-color, #4a6cf7);
}
.trigger-btn.primary {
  background: var(--accent-dim, rgba(74, 108, 247, 0.12));
  border-color: var(--accent-color, #4a6cf7);
  color: var(--accent-color, #4a6cf7);
}
.trigger-btn:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}
</style>
