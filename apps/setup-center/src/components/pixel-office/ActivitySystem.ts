import { EventBus } from './EventBus';
import type { ActivityType } from './StatusMapping';
import { ACTIVITY_PRIORITY } from './StatusMapping';
import type { RoomDef } from './RoomGenerator';

export interface Activity {
  id: string;
  type: ActivityType;
  participants: string[];
  targetRoomId?: string;
  data?: Record<string, unknown>;
  duration: number;
  createdAt: number;
}

interface QueuedActivity extends Activity {
  priority: number;
}

export class ActivitySystem {
  private queue: QueuedActivity[] = [];
  private activeActivities = new Map<string, Activity>();
  private busyNodes = new Set<string>();
  private rooms: RoomDef[] = [];
  private activityIdCounter = 0;

  constructor(rooms: RoomDef[]) {
    this.rooms = rooms;
    this.setupListeners();
  }

  updateRooms(rooms: RoomDef[]) {
    this.rooms = rooms;
  }

  private setupListeners() {
    EventBus.on('org-event', this.handleOrgEvent, this);
  }

  destroy() {
    EventBus.off('org-event', this.handleOrgEvent, this);
    this.queue = [];
    this.activeActivities.clear();
    this.busyNodes.clear();
  }

  private handleOrgEvent = (eventType: string, payload: Record<string, unknown>) => {
    switch (eventType) {
      case 'org:meeting_started':
        this.enqueueMeeting(payload);
        break;
      case 'org:meeting_speak':
        this.enqueueMeetingSpeak(payload);
        break;
      case 'org:meeting_completed':
        this.enqueueMeetingEnd(payload);
        break;
      case 'org:task_delegated':
        this.enqueueTaskDelegate(payload);
        break;
      case 'org:task_delivered':
        this.enqueueTaskDeliver(payload);
        break;
      case 'org:task_accepted':
        this.enqueueSimple('task_accept', payload);
        break;
      case 'org:task_rejected':
        this.enqueueSimple('task_reject', payload);
        break;
      case 'org:escalation':
        this.enqueueEscalation(payload);
        break;
      case 'org:broadcast':
        this.enqueueBroadcast(payload);
        break;
      case 'org:message':
        this.enqueueMessage(payload);
        break;
      case 'org:node_status':
        this.enqueueStatusChange(payload);
        break;
      case 'org:heartbeat_start':
        this.enqueueHeartbeat(payload);
        break;
    }
  };

  private nextId(): string {
    return `act_${++this.activityIdCounter}`;
  }

  private enqueue(activity: Activity) {
    const priority = ACTIVITY_PRIORITY[activity.type] ?? 0;
    this.queue.push({ ...activity, priority });
    this.queue.sort((a, b) => b.priority - a.priority);
    this.processQueue();
  }

  private processQueue() {
    let i = 0;
    while (i < this.queue.length) {
      const next = this.queue[i];
      const canStart = next.participants.every(p => !this.busyNodes.has(p));
      if (!canStart) {
        i++;
        continue;
      }
      this.queue.splice(i, 1);
      this.startActivity(next);
    }
  }

  private startActivity(activity: QueuedActivity) {
    this.activeActivities.set(activity.id, activity);
    activity.participants.forEach(p => this.busyNodes.add(p));

    EventBus.emit('activity-start', activity);

    setTimeout(() => {
      this.endActivity(activity.id);
    }, activity.duration);
  }

  private endActivity(activityId: string) {
    const activity = this.activeActivities.get(activityId);
    if (!activity) return;

    activity.participants.forEach(p => this.busyNodes.delete(p));
    this.activeActivities.delete(activityId);

    EventBus.emit('activity-end', activity);
    this.processQueue();
  }

  private enqueueMeeting(payload: Record<string, unknown>) {
    const participants = (payload.participants as string[]) ?? [];
    const initiator = payload.initiator as string;
    const all = initiator ? [initiator, ...participants] : participants;

    this.enqueue({
      id: this.nextId(),
      type: 'meeting_gather',
      participants: all,
      targetRoomId: 'meeting',
      data: { topic: payload.topic },
      duration: 8000,
      createdAt: Date.now(),
    });
  }

  private enqueueMeetingSpeak(payload: Record<string, unknown>) {
    const speaker = payload.speaker as string ?? payload.node_id as string;
    if (!speaker) return;
    this.enqueue({
      id: this.nextId(),
      type: 'meeting_speak',
      participants: [speaker],
      targetRoomId: 'meeting',
      data: { content: (payload.content as string)?.slice(0, 40) },
      duration: 3000,
      createdAt: Date.now(),
    });
  }

  private enqueueMeetingEnd(payload: Record<string, unknown>) {
    const participants = (payload.participants as string[]) ?? [];
    this.enqueue({
      id: this.nextId(),
      type: 'meeting_end',
      participants,
      data: { conclusion: payload.conclusion },
      duration: 2000,
      createdAt: Date.now(),
    });
  }

  private enqueueTaskDelegate(payload: Record<string, unknown>) {
    const from = payload.from_node as string ?? payload.node_id as string ?? '';
    const to = payload.to_node as string ?? '';
    if (!from || !to) return;
    this.enqueue({
      id: this.nextId(),
      type: 'task_delegate',
      participants: [from, to],
      data: { task: payload.task },
      duration: 3000,
      createdAt: Date.now(),
    });
  }

  private enqueueTaskDeliver(payload: Record<string, unknown>) {
    const from = payload.from_node as string ?? payload.node_id as string ?? '';
    const to = payload.to_node as string ?? '';
    if (!from || !to) return;
    this.enqueue({
      id: this.nextId(),
      type: 'task_deliver',
      participants: [from, to],
      data: { deliverable: payload.deliverable },
      duration: 3000,
      createdAt: Date.now(),
    });
  }

  private enqueueEscalation(payload: Record<string, unknown>) {
    const from = payload.from_node as string ?? payload.node_id as string ?? '';
    const to = payload.to_node as string ?? '';
    if (!from) return;
    this.enqueue({
      id: this.nextId(),
      type: 'escalation',
      participants: to ? [from, to] : [from],
      data: { content: payload.content },
      duration: 3000,
      createdAt: Date.now(),
    });
  }

  private enqueueBroadcast(payload: Record<string, unknown>) {
    const from = payload.from_node as string ?? payload.node_id as string ?? '';
    if (!from) return;
    this.enqueue({
      id: this.nextId(),
      type: 'broadcast',
      participants: [from],
      targetRoomId: 'public',
      data: { content: payload.content },
      duration: 4000,
      createdAt: Date.now(),
    });
  }

  private enqueueMessage(payload: Record<string, unknown>) {
    const from = payload.from_node as string ?? '';
    const to = payload.to_node as string ?? '';
    if (!from || !to) return;
    this.enqueue({
      id: this.nextId(),
      type: 'message',
      participants: [from, to],
      data: { content: (payload.content as string)?.slice(0, 30) },
      duration: 2500,
      createdAt: Date.now(),
    });
  }

  private enqueueStatusChange(payload: Record<string, unknown>) {
    const nodeId = payload.node_id as string ?? '';
    if (!nodeId) return;
    this.enqueue({
      id: this.nextId(),
      type: 'status_change',
      participants: [nodeId],
      data: { status: payload.status },
      duration: 1500,
      createdAt: Date.now(),
    });
  }

  private enqueueHeartbeat(payload: Record<string, unknown>) {
    const participants = (payload.node_ids as string[]) ?? [];
    if (!participants.length) return;
    this.enqueue({
      id: this.nextId(),
      type: 'heartbeat',
      participants,
      duration: 2000,
      createdAt: Date.now(),
    });
  }

  private enqueueSimple(type: ActivityType, payload: Record<string, unknown>) {
    const from = payload.from_node as string ?? payload.node_id as string ?? '';
    const to = payload.to_node as string ?? '';
    if (!from) return;
    this.enqueue({
      id: this.nextId(),
      type,
      participants: to ? [from, to] : [from],
      data: payload,
      duration: 2000,
      createdAt: Date.now(),
    });
  }
}
