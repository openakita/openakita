<script setup lang="ts">
import { ref, computed, watch, reactive } from 'vue'
import { useBestPracticeStore } from '@/stores/bestpractice'
import { useUIStore } from '@/stores/ui'
import { httpClient } from '@/api/http-client'

const bpStore = useBestPracticeStore()
const uiStore = useUIStore()
const confirmStatus = ref<'idle' | 'saving' | 'saved'>('idle')

const instance = computed(() => {
  if (!uiStore.selectedBPInstanceId) return null
  return bpStore.instances.get(uiStore.selectedBPInstanceId) ?? null
})

const subtasks = computed(() => instance.value?.subtasks ?? [])

const selectedSubtask = computed(() =>
  subtasks.value.find(s => s.id === uiStore.selectedSubtaskId) ?? null
)

const hasOutput = computed(() => !!selectedSubtask.value?.output)

// Schema 驱动模式：output 只有 _raw_output 且有 outputSchema
const isSchemaMode = computed(() => {
  const out = selectedSubtask.value?.output
  const schema = selectedSubtask.value?.outputSchema as Record<string, any> | undefined
  if (!out || !schema?.properties) return false
  const keys = Object.keys(out)
  return keys.length === 1 && keys[0] === '_raw_output'
})

const rawOutputText = computed(() =>
  String(selectedSubtask.value?.output?.['_raw_output'] ?? '')
)

const schemaFields = computed(() => {
  const schema = selectedSubtask.value?.outputSchema as Record<string, any> | undefined
  if (!schema?.properties) return []
  return Object.entries(schema.properties).map(([key, meta]: [string, any]) => ({
    key,
    type: meta.type ?? 'string',
    description: meta.description ?? key,
    required: (schema.required ?? []).includes(key),
  }))
})

const rawOutputCollapsed = ref(false)

// Deep clone of output for editing
const editedData = ref<Record<string, unknown>>({})
const originalData = ref<Record<string, unknown>>({})

watch(() => [uiStore.selectedSubtaskId, selectedSubtask.value?.output], () => {
  const out = selectedSubtask.value?.output
  if (out) {
    const schema = selectedSubtask.value?.outputSchema as Record<string, any> | undefined
    const keys = Object.keys(out)
    if (keys.length === 1 && keys[0] === '_raw_output' && schema?.properties) {
      // Schema 模式: try to extract field values from raw output text
      const rawText = String(out['_raw_output'] ?? '')
      const init: Record<string, unknown> = {}

      // Try to find a JSON object in the raw output text
      let extractedJson: Record<string, unknown> | null = null
      const jsonMatch = rawText.match(/\{[\s\S]*\}/)
      if (jsonMatch) {
        try {
          extractedJson = JSON.parse(jsonMatch[0])
        } catch { /* ignore */ }
      }

      for (const key of Object.keys(schema.properties)) {
        init[key] = extractedJson?.[key] ?? ''
      }
      editedData.value = init
      originalData.value = JSON.parse(JSON.stringify(init))
    } else {
      editedData.value = JSON.parse(JSON.stringify(out))
      originalData.value = JSON.parse(JSON.stringify(out))
    }
  } else {
    editedData.value = {}
    originalData.value = {}
  }
  confirmStatus.value = 'idle'
  rawOutputCollapsed.value = false
}, { immediate: true })

function pillClass(st: { id: string; status: string }) {
  if (st.id === uiStore.selectedSubtaskId) return 'active'
  if (st.status === 'done') return 'done'
  return 'upcoming'
}

function switchSubtask(subtaskId: string) {
  if (!uiStore.selectedBPInstanceId) return
  uiStore.openSubtaskOutput(uiStore.selectedBPInstanceId, subtaskId)
}

function fieldType(key: string, value: unknown): string {
  // Check outputSchema first
  const schema = selectedSubtask.value?.outputSchema as Record<string, any> | undefined
  if (schema?.properties?.[key]?.type) return schema.properties[key].type
  // Auto-detect
  if (Array.isArray(value)) return 'array'
  if (typeof value === 'number') return 'number'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'object' && value !== null) return 'object'
  return 'string'
}

function isLongText(value: unknown): boolean {
  return typeof value === 'string' && value.length > 80
}

function updateField(key: string, value: unknown) {
  editedData.value[key] = value
}

function tryParseJsonField(key: string, raw: string) {
  try { updateField(key, JSON.parse(raw)) } catch {}
}

function updateArrayItem(key: string, index: number, value: string) {
  const arr = editedData.value[key] as unknown[]
  arr[index] = value
}

function removeArrayItem(key: string, index: number) {
  const arr = editedData.value[key] as unknown[]
  arr.splice(index, 1)
}

function addArrayItem(key: string) {
  const arr = editedData.value[key] as unknown[]
  arr.push('')
}

async function confirmUpdate() {
  if (!uiStore.selectedBPInstanceId || !uiStore.selectedSubtaskId) return
  confirmStatus.value = 'saving'
  try {
    // Compute diff (changed fields only)
    const changes: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(editedData.value)) {
      if (JSON.stringify(v) !== JSON.stringify(originalData.value[k])) {
        changes[k] = v
      }
    }
    if (Object.keys(changes).length === 0) {
      confirmStatus.value = 'idle'
      return
    }
    await httpClient.editBPOutput(uiStore.selectedBPInstanceId, uiStore.selectedSubtaskId, changes)
    originalData.value = JSON.parse(JSON.stringify(editedData.value))
    confirmStatus.value = 'saved'
    setTimeout(() => { confirmStatus.value = 'idle' }, 1500)
  } catch (e) {
    console.error('Failed to update output:', e)
    confirmStatus.value = 'idle'
  }
}

function resetData() {
  editedData.value = JSON.parse(JSON.stringify(originalData.value))
}
</script>

<template>
  <div class="subtask-output-panel">
    <!-- Step Navigator Pills -->
    <div class="step-navigator">
      <div class="step-nav-label">子任务进度</div>
      <div class="step-nav-pills">
        <div
          v-for="st in subtasks"
          :key="st.id"
          :class="['step-nav-pill', pillClass(st)]"
          @click="switchSubtask(st.id)"
        >
          <span class="dot"></span>
          <span>{{ st.name }}</span>
        </div>
      </div>
    </div>

    <!-- Panel Content -->
    <div class="panel-content">
      <template v-if="hasOutput">
        <div class="output-section">
          <!-- Schema 驱动模式: _raw_output + outputSchema -->
          <template v-if="isSchemaMode">
            <div class="raw-reference">
              <div class="raw-ref-header" @click="rawOutputCollapsed = !rawOutputCollapsed">
                <span class="material-symbols-rounded">{{ rawOutputCollapsed ? 'expand_more' : 'expand_less' }}</span>
                <span>原始输出（参考）</span>
              </div>
              <pre v-show="!rawOutputCollapsed" class="raw-ref-content">{{ rawOutputText }}</pre>
            </div>

            <div class="output-section-label">
              <span class="material-symbols-rounded">edit_note</span>
              下一步所需数据（请根据上方原始输出填写）
            </div>
            <div class="json-editor">
              <div v-for="field in schemaFields" :key="field.key" class="json-field">
                <div class="field-key">
                  {{ field.key }}
                  <span v-if="field.required" class="required-mark">*</span>
                </div>
                <div class="field-value">
                  <div class="field-desc">{{ field.description }}</div>
                  <textarea
                    v-if="field.type === 'object' || field.type === 'array'"
                    class="field-input"
                    :value="typeof editedData[field.key] === 'object'
                      ? JSON.stringify(editedData[field.key], null, 2)
                      : String(editedData[field.key] ?? '')"
                    @input="tryParseJsonField(field.key, ($event.target as HTMLTextAreaElement).value)"
                    :placeholder="field.type === 'array' ? '输入 JSON 数组...' : '输入 JSON 对象...'"
                  ></textarea>
                  <input
                    v-else-if="field.type === 'number'"
                    class="field-input"
                    type="number"
                    :value="editedData[field.key]"
                    @input="updateField(field.key, Number(($event.target as HTMLInputElement).value))"
                  />
                  <textarea
                    v-else
                    class="field-input"
                    :value="String(editedData[field.key] ?? '')"
                    @input="updateField(field.key, ($event.target as HTMLTextAreaElement).value)"
                    :placeholder="field.description"
                  ></textarea>
                </div>
              </div>
            </div>
          </template>

          <!-- 普通模式: 直接编辑 output 字段 -->
          <template v-else>
            <div class="output-section-label">
              <span class="material-symbols-rounded">edit_note</span>
              输出数据（可编辑）
            </div>
            <div class="json-editor">
              <div v-for="(value, key) in editedData" :key="String(key)" class="json-field">
                <div class="field-key">{{ key }}</div>
                <div class="field-value">
                  <!-- Array field -->
                  <div v-if="fieldType(String(key), value) === 'array'" class="array-field">
                    <div v-for="(item, idx) in (value as unknown[])" :key="idx" class="array-item">
                      <input
                        class="field-input"
                        :value="typeof item === 'object' ? JSON.stringify(item) : String(item)"
                        @input="updateArrayItem(String(key), idx, ($event.target as HTMLInputElement).value)"
                      />
                      <button class="remove-btn" @click="removeArrayItem(String(key), idx)">
                        <span class="material-symbols-rounded" style="font-size:14px">close</span>
                      </button>
                    </div>
                    <div class="array-actions">
                      <button class="add-btn" @click="addArrayItem(String(key))">
                        <span class="material-symbols-rounded">add</span>新增
                      </button>
                    </div>
                  </div>
                  <!-- Long text / textarea -->
                  <textarea
                    v-else-if="fieldType(String(key), value) === 'string' && isLongText(value)"
                    class="field-input"
                    :value="String(value)"
                    @input="updateField(String(key), ($event.target as HTMLTextAreaElement).value)"
                  ></textarea>
                  <!-- Object → JSON textarea -->
                  <textarea
                    v-else-if="fieldType(String(key), value) === 'object'"
                    class="field-input"
                    :value="JSON.stringify(value, null, 2)"
                    @input="tryParseJsonField(String(key), ($event.target as HTMLTextAreaElement).value)"
                  ></textarea>
                  <!-- Number -->
                  <input
                    v-else-if="fieldType(String(key), value) === 'number'"
                    class="field-input"
                    type="number"
                    :value="value"
                    @input="updateField(String(key), Number(($event.target as HTMLInputElement).value))"
                  />
                  <!-- Default: string input -->
                  <input
                    v-else
                    class="field-input"
                    :value="String(value ?? '')"
                    @input="updateField(String(key), ($event.target as HTMLInputElement).value)"
                  />
                </div>
              </div>
            </div>
          </template>
        </div>
      </template>
      <div v-else class="empty-state">
        <span class="material-symbols-rounded">hourglass_empty</span>
        <p>子任务尚未执行完成<br>完成后将在此处展示可编辑的输出数据</p>
      </div>
    </div>

    <!-- Confirm Bar -->
    <div v-if="hasOutput" class="confirm-update-bar">
      <button class="reset-btn" @click="resetData">重置</button>
      <button
        :class="['confirm-btn', { saved: confirmStatus === 'saved' }]"
        :disabled="confirmStatus === 'saving'"
        @click="confirmUpdate"
      >
        {{ confirmStatus === 'saved' ? '✓ 已更新' : confirmStatus === 'saving' ? '更新中...' : '确认更新' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.subtask-output-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* Step Navigator */
.step-navigator {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-subtle, #2a2a4a);
  flex-shrink: 0;
}
.step-nav-label {
  font-size: 11px;
  color: var(--text-ghost, #555);
  margin-bottom: 10px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.step-nav-pills { display: flex; gap: 6px; flex-wrap: wrap; }
.step-nav-pill {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 6px;
  font-size: 12px; font-weight: 500;
  cursor: pointer; transition: all 0.15s;
  border: 1px solid transparent;
}
.step-nav-pill .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.step-nav-pill.done .dot { background: var(--success-color, #4caf50); }
.step-nav-pill.active .dot { background: var(--accent-color, #4a6cf7); }
.step-nav-pill.upcoming .dot { background: var(--text-ghost, #555); }
.step-nav-pill.done { background: rgba(76,175,80,0.1); color: var(--success-color, #4caf50); }
.step-nav-pill.active { background: rgba(74,108,247,0.1); color: var(--accent-color, #4a6cf7); border-color: var(--accent-color, #4a6cf7); }
.step-nav-pill.upcoming { background: var(--bg-surface, #1a1a2e); color: var(--text-ghost, #555); }

/* Panel Content */
.panel-content { flex: 1; overflow-y: auto; padding: 18px; }

/* Output Section */
.output-section-label {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-ghost, #555);
  margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
}
.output-section-label .material-symbols-rounded { font-size: 14px; }

/* JSON Editor */
.json-editor {
  background: var(--bg-surface, #1a1a2e);
  border: 1px solid var(--border-subtle, #2a2a4a);
  border-radius: 8px; overflow: hidden;
}
.json-field {
  padding: 10px 14px; border-bottom: 1px solid var(--border-subtle, #2a2a4a);
  display: flex; align-items: flex-start; gap: 10px;
}
.json-field:last-child { border-bottom: none; }
.field-key {
  font-family: 'JetBrains Mono', monospace; font-size: 12px;
  color: var(--accent-color, #4a6cf7); min-width: 80px;
  padding-top: 4px; flex-shrink: 0;
}
.field-value { flex: 1; }
.field-input {
  width: 100%; padding: 6px 10px;
  background: var(--bg-elevated, #1e1e38);
  border: 1px solid var(--border-color, #2a2a4a);
  border-radius: 4px; color: var(--text-primary, #e0e0e0);
  font-size: 13px; font-family: inherit; outline: none;
  transition: border-color 0.15s; box-sizing: border-box;
}
.field-input:focus { border-color: var(--accent-color, #4a6cf7); }
textarea.field-input { resize: vertical; min-height: 60px; line-height: 1.5; }

/* Array Fields */
.array-item { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.array-item .field-input { flex: 1; }
.remove-btn {
  width: 24px; height: 24px; border-radius: 4px;
  background: rgba(244,67,54,0.1); border: none;
  color: var(--error-color, #f44336); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s; flex-shrink: 0;
}
.remove-btn:hover { background: rgba(244,67,54,0.2); }
.array-actions { display: flex; gap: 6px; margin-top: 4px; }
.add-btn {
  padding: 4px 10px; background: rgba(74,108,247,0.1);
  border: 1px solid rgba(74,108,247,0.15); border-radius: 4px;
  color: var(--accent-color, #4a6cf7); font-size: 12px;
  cursor: pointer; display: flex; align-items: center; gap: 4px;
  transition: all 0.15s;
}
.add-btn:hover { background: rgba(74,108,247,0.2); }

/* Empty State */
.empty-state {
  text-align: center; padding: 40px 0; color: var(--text-ghost, #555);
}
.empty-state .material-symbols-rounded { font-size: 36px; margin-bottom: 12px; display: block; }
.empty-state p { font-size: 13px; line-height: 1.6; margin: 0; }

/* Confirm Bar */
.confirm-update-bar {
  padding: 12px 18px; border-top: 1px solid var(--border-subtle, #2a2a4a);
  display: flex; justify-content: flex-end; gap: 8px; flex-shrink: 0;
}
.reset-btn {
  padding: 8px 16px; background: transparent;
  border: 1px solid var(--border-color, #2a2a4a); border-radius: 6px;
  color: var(--text-secondary, #888); font-size: 13px;
  cursor: pointer; transition: all 0.15s;
}
.reset-btn:hover { border-color: var(--text-secondary, #888); }
.confirm-btn {
  padding: 8px 20px; background: var(--accent-color, #4a6cf7);
  border: none; border-radius: 6px; color: #fff;
  font-size: 13px; font-weight: 600; cursor: pointer;
  transition: all 0.15s;
}
.confirm-btn:hover { opacity: 0.9; }
.confirm-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.confirm-btn.saved { background: var(--success-color, #4caf50); }

/* Raw Reference (schema mode) */
.raw-reference {
  margin-bottom: 14px;
  border: 1px solid var(--border-subtle, #2a2a4a);
  border-radius: 8px;
  overflow: hidden;
}
.raw-ref-header {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px; cursor: pointer;
  font-size: 12px; font-weight: 500;
  color: var(--text-secondary, #888);
  background: var(--bg-surface, #1a1a2e);
  transition: background 0.15s;
}
.raw-ref-header:hover { background: var(--bg-elevated, #1e1e38); }
.raw-ref-header .material-symbols-rounded { font-size: 16px; }
.raw-ref-content {
  padding: 10px 14px; margin: 0;
  font-size: 12px; line-height: 1.6;
  color: var(--text-secondary, #888);
  background: var(--bg-elevated, #1e1e38);
  max-height: 200px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-word;
  border-top: 1px solid var(--border-subtle, #2a2a4a);
}
.field-desc {
  font-size: 11px;
  color: var(--text-ghost, #555);
  margin-bottom: 4px;
}
.required-mark {
  color: var(--error-color, #f44336);
  margin-left: 2px;
  font-weight: 700;
}
</style>
