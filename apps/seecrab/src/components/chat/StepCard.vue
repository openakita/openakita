<template>
  <div class="step-card" :class="[card.status, { 'is-sub-agent': card.agentId && card.agentId !== 'main' }]" @click="uiStore.selectStep(card.stepId)">
    <span class="status-icon material-symbols-rounded">
      {{ card.status === 'completed' ? 'check_circle' : card.status === 'failed' ? 'error' : 'pending' }}
    </span>
    <span class="card-type-icon material-symbols-rounded">{{ cardTypeIcon }}</span>
    <span class="title">{{ card.title }}</span>
    <span v-if="displayDuration" :class="['duration', { 'live': isRunningTimer }]">{{ displayDuration }}</span>
    <span class="arrow material-symbols-rounded">chevron_right</span>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { useUIStore } from '@/stores/ui'
import type { StepCard } from '@/types'

const props = defineProps<{ card: StepCard }>()
const uiStore = useUIStore()

const cardTypeIcon = computed(() => {
  const map: Record<string, string> = {
    search: 'search', code: 'code', file: 'description',
    analysis: 'analytics', browser: 'language', delegate: 'smart_toy', default: 'build',
  }
  return map[props.card.cardType] ?? 'build'
})

// Gap 6: client-side live timer
const startTime = ref<number | null>(null)
const liveSeconds = ref(0)
let rafId: number | null = null

function stopTimer() {
  if (rafId != null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
}

watch(() => props.card.status, (status) => {
  if (status === 'running' && !startTime.value) {
    startTime.value = Date.now()
    const tick = () => {
      liveSeconds.value = (Date.now() - startTime.value!) / 1000
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
  } else if (status !== 'running') {
    stopTimer()
  }
}, { immediate: true })

onBeforeUnmount(stopTimer)

const displayDuration = computed(() => {
  if (props.card.status === 'running') {
    return liveSeconds.value.toFixed(1) + 's'
  }
  if (props.card.duration != null) {
    return props.card.duration + 's'
  }
  return null
})

const isRunningTimer = computed(() => props.card.status === 'running' && startTime.value != null)
</script>

<style scoped>
.step-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  cursor: pointer;
  margin-bottom: 4px;
  font-size: 13px;
  transition: all 0.15s var(--ease-out);
  animation: fadeIn 0.3s var(--ease-out) both;
  position: relative;
}
.step-card:hover {
  background: var(--bg-elevated);
  border-color: var(--border);
  transform: translateX(2px);
}
.step-card.completed {
  border-left: 2px solid var(--success);
}
.step-card.running {
  border-left: 2px solid var(--accent);
}
.step-card.failed {
  border-left: 2px solid var(--error);
}
.step-card.is-sub-agent {
  margin-left: 24px;
  background: color-mix(in srgb, var(--accent) 4%, var(--bg-surface));
  border-color: color-mix(in srgb, var(--accent) 15%, var(--border-subtle));
}
.step-card.is-sub-agent.completed {
  border-left-color: var(--accent);
}
.step-card.is-sub-agent.running {
  border-left-color: var(--accent);
}

.status-icon { font-size: 15px; }
.completed .status-icon { color: var(--success); }
.running .status-icon { color: var(--accent); animation: pulse 1.5s infinite; }
.failed .status-icon { color: var(--error); }

.card-type-icon { font-size: 14px; color: var(--text-ghost); }
.title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}
.duration {
  color: var(--text-ghost);
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  font-variant-numeric: tabular-nums;
}
.duration.live {
  color: var(--accent);
  animation: pulse 1.5s infinite;
}
.arrow {
  color: var(--text-ghost);
  font-size: 16px;
  transition: all 0.15s;
}
.step-card:hover .arrow {
  color: var(--accent);
  transform: translateX(2px);
}
</style>
