<template>
  <div class="welcome">
    <div class="welcome-inner">
      <div class="logo-mark">
        <span class="material-symbols-rounded logo-icon">smart_toy</span>
      </div>
      <h1 class="title">OpenCrab</h1>
      <p class="subtitle">
        自进化 AI Agent — 永不放弃的 Ralph Wiggum 模式<br>
        支持自主学习、记忆管理、多 LLM 端点
      </p>
      <div class="shortcuts">
        <button
          v-for="(s, i) in shortcuts"
          :key="s.label"
          class="shortcut"
          :style="{ animationDelay: `${300 + i * 80}ms` }"
          @click="$emit('prefill', s.prefill)"
        >
          <span class="material-symbols-rounded shortcut-icon">{{ s.icon }}</span>
          <span class="shortcut-label">{{ s.label }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineEmits<{ prefill: [text: string] }>()
const shortcuts = [
  { icon: 'search', label: '搜索最新 AI 观点', prefill: '帮我搜索最新 AI 观点' },
  { icon: 'hub', label: '分析代码库架构', prefill: '帮我分析代码库架构' },
  { icon: 'translate', label: '翻译技术文档', prefill: '帮我翻译技术文档' },
  { icon: 'draw', label: '生成技术方案', prefill: '帮我生成技术方案' },
]
</script>

<style scoped>
.welcome {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

/* On wide screens, offset content to center relative to viewport (not just ChatArea) */
@media (min-width: 1400px) {
  .welcome {
    padding-right: var(--sidebar-width);
  }
}

.welcome-inner {
  text-align: center;
  animation: fadeIn 0.6s var(--ease-out) both;
  width: 100%;
  max-width: var(--chat-max-width);
  padding: 0 24px;
}

/* ── Large gradient icon ── */
.logo-mark {
  width: 88px;
  height: 88px;
  margin: 0 auto 20px;
  border-radius: 22px;
  background: linear-gradient(145deg, #6366f1, #38bdcc);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow:
    0 8px 32px rgba(99, 102, 241, 0.25),
    0 0 60px rgba(56, 189, 204, 0.1);
  animation: float 5s ease-in-out infinite;
  position: relative;
}
.logo-mark::after {
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: 23px;
  background: linear-gradient(145deg, rgba(255,255,255,0.15), transparent);
  pointer-events: none;
}
.logo-icon {
  font-size: 44px;
  color: white;
  font-variation-settings: 'FILL' 1;
}

.title {
  font-size: 32px;
  font-weight: 700;
  color: var(--text-bright);
  letter-spacing: -0.03em;
  margin-bottom: 8px;
}

.subtitle {
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 400;
  margin-bottom: 40px;
  line-height: 1.7;
  letter-spacing: 0.01em;
}

/* ── 2x2 shortcut grid ── */
.shortcuts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.shortcut {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 14px;
  font-family: inherit;
  font-weight: 500;
  transition: all 0.2s var(--ease-out);
  animation: fadeIn 0.4s var(--ease-out) both;
  text-align: left;
}
.shortcut:hover {
  background: var(--bg-elevated);
  border-color: var(--border-accent);
  transform: translateY(-1px);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
}

.shortcut-icon {
  font-size: 20px;
  color: var(--accent);
  flex-shrink: 0;
}
.shortcut-label {
  flex: 1;
}
</style>
