// apps/seecrab/src/stores/bestpractice.ts
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { BPInstanceState, BPSubtaskInfo, BPRunMode } from '@/types'

export const useBestPracticeStore = defineStore('bestpractice', () => {
  const instances = ref<Map<string, BPInstanceState>>(new Map())
  const activeInstanceId = ref<string | null>(null)

  const activeInstance = computed(() =>
    activeInstanceId.value ? instances.value.get(activeInstanceId.value) ?? null : null
  )

  function updateFromProgress(event: {
    instance_id: string
    bp_name: string
    statuses: Record<string, string>
    subtasks?: { id: string; name: string }[]
    current_subtask_index: number
    run_mode: string
    status: string
  }) {
    const existing = instances.value.get(event.instance_id)
    const subtaskSource = event.subtasks ?? Object.keys(event.statuses).map(id => ({ id, name: id }))
    const subtasks: BPSubtaskInfo[] = subtaskSource.map(st => ({
      id: st.id,
      name: st.name ?? existing?.subtasks.find(s => s.id === st.id)?.name ?? st.id,
      status: (event.statuses[st.id] ?? 'pending') as BPSubtaskInfo['status'],
      output: existing?.subtasks.find(s => s.id === st.id)?.output,
      outputSchema: existing?.subtasks.find(s => s.id === st.id)?.outputSchema,
      summary: existing?.subtasks.find(s => s.id === st.id)?.summary,
    }))

    const state: BPInstanceState = {
      instanceId: event.instance_id,
      bpId: existing?.bpId ?? '',
      bpName: event.bp_name,
      status: event.status as BPInstanceState['status'],
      runMode: event.run_mode as BPRunMode,
      subtasks,
      currentSubtaskIndex: event.current_subtask_index,
    }
    instances.value.set(event.instance_id, state)
    activeInstanceId.value = event.instance_id
  }

  function updateSubtaskOutput(
    instanceId: string,
    subtaskId: string,
    output: Record<string, unknown>,
    extra?: { summary?: string; outputSchema?: Record<string, unknown>; subtaskName?: string },
  ) {
    const inst = instances.value.get(instanceId)
    if (!inst) return
    const st = inst.subtasks.find(s => s.id === subtaskId)
    if (st) {
      st.output = output
      st.status = 'done'
      if (extra?.summary) st.summary = extra.summary
      if (extra?.outputSchema) st.outputSchema = extra.outputSchema
      if (extra?.subtaskName) st.name = extra.subtaskName
    }
  }

  function markStale(instanceId: string, staleIds: string[]) {
    const inst = instances.value.get(instanceId)
    if (!inst) return
    for (const id of staleIds) {
      const st = inst.subtasks.find(s => s.id === id)
      if (st) st.status = 'stale'
    }
  }

  function clear() {
    instances.value.clear()
    activeInstanceId.value = null
  }

  return {
    instances,
    activeInstanceId,
    activeInstance,
    updateFromProgress,
    updateSubtaskOutput,
    markStale,
    clear,
  }
})
