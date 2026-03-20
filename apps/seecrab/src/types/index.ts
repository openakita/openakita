// apps/seecrab/src/types/index.ts

export type SSEEventType =
  | 'thinking' | 'plan_checklist' | 'step_card' | 'ai_text'
  | 'ask_user' | 'agent_header'
  | 'timer_update' | 'heartbeat' | 'done' | 'error'
  | 'session_title'
  | 'bp_progress' | 'bp_subtask_output' | 'bp_stale'

export interface SSEEvent {
  type: SSEEventType
  [key: string]: unknown
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  reply?: ReplyState
}

export interface ReplyState {
  replyId: string
  agentId: string
  agentName: string
  thinking: string
  thinkingDone: boolean
  planChecklist: PlanStep[] | null
  stepCards: StepCard[]
  summaryText: string
  agentSummaries: Record<string, string>
  timer: TimerState
  askUser: AskUserState | null
  isDone: boolean
  bpProgress: BPInstanceState | null
  bpSubtaskOutput: { subtaskId: string; output: Record<string, unknown>; summary?: string } | null
}

export interface StepCard {
  stepId: string
  title: string
  status: 'running' | 'completed' | 'failed'
  sourceType: 'tool' | 'skill' | 'mcp' | 'plan_step'
  cardType: 'search' | 'code' | 'file' | 'analysis' | 'browser' | 'default'
  duration: number | null
  planStepIndex: number | null
  agentId: string
  input: Record<string, unknown> | null
  output: string | null
  absorbedCalls: AbsorbedCall[]
  // SSE snake_case fields (pre-mapping)
  step_id?: string
  source_type?: string
  card_type?: string
  plan_step_index?: number | null
  agent_id?: string
  absorbed_calls?: AbsorbedCall[]
}

export interface AbsorbedCall {
  tool: string
  tool_id: string
  args: Record<string, unknown>
  duration: number | null
  result?: string
  is_error?: boolean
}

export interface TimerState {
  ttft: { state: 'idle' | 'running' | 'done' | 'cancelled'; value: number | null }
  total: { state: 'idle' | 'running' | 'done' | 'cancelled'; value: number | null }
}

export interface PlanStep {
  index: number
  title: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

export interface AskUserState {
  ask_id: string
  question: string
  options: { label: string; value: string }[]
  answered: boolean
  answer?: string
}

export interface Session {
  id: string
  title: string
  lastMessage: string
  updatedAt: number
  messageCount: number
}

export type BPSubtaskStatus = 'pending' | 'current' | 'done' | 'failed' | 'stale'
export type BPRunMode = 'manual' | 'auto'
export type BPInstanceStatus = 'active' | 'suspended' | 'completed' | 'cancelled'

export interface BPSubtaskInfo {
  id: string
  name: string
  status: BPSubtaskStatus
  output?: Record<string, unknown>
  outputSchema?: Record<string, unknown>
  summary?: string
}

export interface BPInstanceState {
  instanceId: string
  bpId: string
  bpName: string
  status: BPInstanceStatus
  runMode: BPRunMode
  subtasks: BPSubtaskInfo[]
  currentSubtaskIndex: number
}

export interface BPProgressEvent {
  type: 'bp_progress'
  instance_id: string
  bp_name: string
  statuses: Record<string, string>
  subtasks: { id: string; name: string }[]
  current_subtask_index: number
  run_mode: string
  status: string
}

export interface BPSubtaskOutputEvent {
  type: 'bp_subtask_output'
  instance_id: string
  subtask_id: string
  subtask_name: string
  output: Record<string, unknown>
  output_schema?: Record<string, unknown>
  summary?: string
}

export interface BPStaleEvent {
  type: 'bp_stale'
  instance_id: string
  stale_subtask_ids: string[]
  reason: string
}
