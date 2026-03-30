import { useEffect, useRef } from 'react';

export interface EventLogEntry {
  type: string;
  participants: string[];
  data?: Record<string, unknown>;
  time: number;
}

const TYPE_EMOJI: Record<string, string> = {
  meeting_gather: '🤝',
  meeting_speak: '💬',
  meeting_end: '✅',
  task_delegate: '📋',
  task_deliver: '📦',
  task_accept: '👍',
  task_reject: '❌',
  escalation: '❗',
  broadcast: '📢',
  message: '💬',
  status_change: '🔄',
  heartbeat: '💓',
};

const TYPE_LABEL: Record<string, string> = {
  meeting_gather: '开会',
  meeting_speak: '发言',
  meeting_end: '散会',
  task_delegate: '派发',
  task_deliver: '交付',
  task_accept: '通过',
  task_reject: '驳回',
  escalation: '上报',
  broadcast: '广播',
  message: '消息',
  status_change: '状态',
  heartbeat: '心跳',
};

function formatTime(ts: number): string {
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`;
}

export function PixelOfficeEventLog({ entries }: { entries: EventLogEntry[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries.length]);

  return (
    <div className="pixelPanel">
      <div className="pixelPanelTitle">📜 事件日志</div>
      <div className="pixelPanelContent" ref={scrollRef}>
        {entries.length === 0 && (
          <div style={{ color: 'var(--muted)', fontSize: 10, padding: 8 }}>
            等待组织事件...
          </div>
        )}
        {entries.map((e, i) => (
          <div key={i} className="eventLogEntry">
            <span className="eventLogTime">{formatTime(e.time)}</span>
            <span className="eventLogType">
              {TYPE_EMOJI[e.type] ?? '·'} {TYPE_LABEL[e.type] ?? e.type}
            </span>
            <span className="eventLogMsg">
              {e.participants.join(', ')}
              {e.data?.content ? ` — ${String(e.data.content).slice(0, 30)}` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
