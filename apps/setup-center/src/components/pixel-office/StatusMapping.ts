export type NodeStatus = 'idle' | 'busy' | 'waiting' | 'error' | 'offline' | 'frozen';

export type AreaType = 'department' | 'meeting' | 'break' | 'debug' | 'entrance' | 'public';

export interface AreaDef {
  type: AreaType;
  label: string;
}

export const STATUS_TO_AREA: Record<NodeStatus, AreaType> = {
  idle: 'department',
  busy: 'department',
  waiting: 'department',
  error: 'debug',
  offline: 'entrance',
  frozen: 'department',
};

export const STATUS_ANIMATION: Record<NodeStatus, string> = {
  idle: 'idle',
  busy: 'type',
  waiting: 'idle',
  error: 'idle',
  offline: 'idle',
  frozen: 'idle',
};

export type ActivityType =
  | 'meeting_gather'
  | 'meeting_speak'
  | 'meeting_end'
  | 'task_delegate'
  | 'task_deliver'
  | 'task_accept'
  | 'task_reject'
  | 'escalation'
  | 'broadcast'
  | 'message'
  | 'status_change'
  | 'heartbeat';

export const ACTIVITY_PRIORITY: Record<ActivityType, number> = {
  meeting_gather: 100,
  meeting_speak: 95,
  meeting_end: 90,
  escalation: 80,
  task_delegate: 70,
  task_deliver: 65,
  task_accept: 60,
  task_reject: 60,
  broadcast: 50,
  message: 40,
  status_change: 20,
  heartbeat: 10,
};
