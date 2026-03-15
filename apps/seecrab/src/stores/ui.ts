// apps/seecrab/src/stores/ui.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUIStore = defineStore('ui', () => {
  const rightPanelOpen = ref(false)
  const selectedStepId = ref<string | null>(null)
  const thinkingExpanded = ref(false)

  function selectStep(stepId: string) {
    selectedStepId.value = stepId
    rightPanelOpen.value = true
  }

  function closeRightPanel() {
    rightPanelOpen.value = false
    selectedStepId.value = null
  }

  function toggleThinking() {
    thinkingExpanded.value = !thinkingExpanded.value
  }

  return { rightPanelOpen, selectedStepId, thinkingExpanded, selectStep, closeRightPanel, toggleThinking }
})
