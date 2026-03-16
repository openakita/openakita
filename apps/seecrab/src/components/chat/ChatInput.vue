<template>
  <div class="chat-input-container">
    <div class="input-wrapper" :class="{ focused: isFocused }">
      <textarea
        ref="inputRef"
        v-model="inputText"
        placeholder="输入消息..."
        rows="1"
        @keydown.enter.exact.prevent="send"
        @input="autoResize"
        @focus="isFocused = true"
        @blur="isFocused = false"
      />
      <button class="send-btn" :disabled="!inputText.trim() || isStreaming" @click="send">
        <span class="material-symbols-rounded">send</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'
import { sseClient } from '@/api/sse-client'

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement>()
const isStreaming = ref(false)
const isFocused = ref(false)

function autoResize() {
  const el = inputRef.value
  if (el) { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px' }
}

async function send() {
  const msg = inputText.value.trim()
  if (!msg || isStreaming.value) return
  inputText.value = ''

  // Auto-create session if none active (graceful — send even if creation fails)
  if (!sessionStore.activeSessionId) {
    try {
      await sessionStore.createSession()
    } catch (e) {
      console.warn('[ChatInput] Failed to create session, sending without session:', e)
    }
  }

  chatStore.addUserMessage(msg)
  isStreaming.value = true
  try {
    await sseClient.sendMessage(msg, sessionStore.activeSessionId ?? undefined)
  } finally {
    isStreaming.value = false
  }
}

defineExpose({ prefill: (text: string) => { inputText.value = text } })
</script>

<style scoped>
.chat-input-container {
  padding: 16px 24px 24px;
  width: 100%;
  max-width: var(--chat-max-width);
  margin: 0 auto;
}
.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: var(--bg-elevated);
  border-radius: var(--radius-xl);
  padding: 12px 14px 12px 20px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  transition: all 0.25s var(--ease-out);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.2);
}
.input-wrapper.focused {
  border-color: var(--border-accent);
  box-shadow: 0 0 0 3px var(--accent-dim), 0 4px 24px rgba(0, 0, 0, 0.3);
  background: var(--bg-hover);
}
textarea {
  flex: 1;
  background: none;
  border: none;
  color: var(--text-bright);
  font-size: 15px;
  resize: none;
  outline: none;
  max-height: 120px;
  font-family: inherit;
  line-height: 1.5;
}
textarea::placeholder { color: var(--text-muted); }
.send-btn {
  background: var(--accent);
  border: none;
  color: var(--bg-abyss);
  border-radius: 50%;
  width: 34px;
  height: 34px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-weight: 700;
  transition: all 0.15s var(--ease-out);
}
.send-btn .material-symbols-rounded { font-size: 18px; }
.send-btn:hover:not(:disabled) {
  background: var(--accent-bright);
  transform: scale(1.08);
  box-shadow: 0 0 16px var(--accent-glow);
}
.send-btn:disabled {
  background: var(--bg-surface);
  color: var(--text-ghost);
  cursor: not-allowed;
}
</style>
