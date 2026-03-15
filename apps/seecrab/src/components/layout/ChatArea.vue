<!-- apps/seecrab/src/components/layout/ChatArea.vue -->
<template>
  <main class="chat-area">
    <WelcomePage v-if="chatStore.messages.length === 0 && !chatStore.currentReply" />
    <MessageList v-else />
    <ChatInput />
  </main>
</template>

<script setup lang="ts">
import { useChatStore } from '@/stores/chat'
import WelcomePage from '@/components/welcome/WelcomePage.vue'
import MessageList from '@/components/chat/MessageList.vue'
import ChatInput from '@/components/chat/ChatInput.vue'

const chatStore = useChatStore()
</script>

<style scoped>
.chat-area {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-primary);
}
</style>
