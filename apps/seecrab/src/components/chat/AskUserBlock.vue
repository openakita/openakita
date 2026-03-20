<template>
  <div class="ask-user" :class="{ answered: ask.answered }">
    <div class="ask-header">
      <span class="material-symbols-rounded ask-icon">help</span>
      <p class="question">{{ ask.question }}</p>
    </div>
    <div class="options">
      <button
        v-for="opt in ask.options"
        :key="opt.value"
        class="option-btn"
        :class="{ selected: ask.answered && ask.answer === opt.value }"
        :disabled="ask.answered"
        @click="submitAnswer(opt.label, opt.value)"
      >
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'
import { sseClient } from '@/api/sse-client'
import type { AskUserState } from '@/types'

const props = defineProps<{ ask: AskUserState }>()
const chatStore = useChatStore()
const sessionStore = useSessionStore()

async function submitAnswer(label: string, value: string) {
  if (props.ask.answered) return

  // Mark as answered (disables buttons)
  props.ask.answered = true
  props.ask.answer = value

  // For BP trigger ask_user, send bp_id context so LLM knows which BP to start
  const isBpTrigger = props.ask.ask_id?.startsWith('bp_trigger_')
  const displayText = label
  const backendMsg = isBpTrigger && value !== 'free'
    ? `请启用最佳实践 (${value})`
    : label

  // Send as a new user message to continue the conversation
  const convId = sessionStore.activeSessionId
  chatStore.addUserMessage(displayText)
  if (convId) {
    await sseClient.sendMessage(backendMsg, convId, { thinking_mode: 'auto' })
  }
}
</script>

<style scoped>
.ask-user {
  margin: 10px 0;
  padding: 14px 16px;
  background: var(--bg-surface);
  border: 1px solid var(--border-accent);
  border-radius: var(--radius-md);
  animation: fadeIn 0.3s var(--ease-out) both;
}
.ask-user.answered {
  opacity: 0.7;
}
.ask-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}
.ask-icon {
  font-size: 18px;
  color: var(--accent);
  flex-shrink: 0;
  margin-top: 1px;
}
.question {
  font-size: 14px;
  color: var(--text-bright);
  line-height: 1.5;
}
.options {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding-left: 26px;
}
.option-btn {
  padding: 7px 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  transition: all 0.15s var(--ease-out);
}
.option-btn:hover:not(:disabled) {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: var(--accent);
}
.option-btn:disabled {
  cursor: default;
  opacity: 0.5;
}
.option-btn.selected {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: var(--accent);
  opacity: 1;
}
</style>
