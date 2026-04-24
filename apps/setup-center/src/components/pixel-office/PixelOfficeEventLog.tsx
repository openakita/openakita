import { useEffect, useRef } from 'react';

export interface EventLogEntry {
  type: string;
  participants: string[];
  data?: Record<string, unknown>;
  time: number;
}

const TYPE_TAG: Record<string, { label: string; color: string }> = {
  meeting_gather: { label: '开会', color: '#3498db' },
  meeting_speak: { label: '发言', color: '#2ecc71' },
  meeting_end: { label: '散会', color: '#95a5a6' },
  task_delegate: { label: '派发', color: '#e67e22' },
  task_deliver: { label: '交付', color: '#9b59b6' },
  task_accept: { label: '通过', color: '#27ae60' },
  task_reject: { label: '驳回', color: '#e74c3c' },
  escalation: { label: '上报', color: '#e74c3c' },
  broadcast: { label: '广播', color: '#f39c12' },
  message: { label: '消息', color: '#3498db' },
  status_change: { label: '状态', color: '#95a5a6' },
  heartbeat: { label: '心跳', color: '#555' },
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
    <div className="poPanel">
      <div className="poPanelHeader">事件日志</div>
      <div className="poPanelBody" ref={scrollRef}>
        {entries.length === 0 && (
          <div className="poEmpty">等待组织事件…</div>
        )}
        {entries.map((e, i) => {
          const tag = TYPE_TAG[e.type] ?? { label: e.type, color: '#888' };
          return (
            <div key={i} className="poLogRow">
              <span className="poLogTime">{formatTime(e.time)}</span>
              <span className="poLogTag" style={{ background: tag.color }}>{tag.label}</span>
              <span className="poLogMsg">
                {e.participants.join(', ')}
                {e.data?.content ? ` — ${String(e.data.content).slice(0, 30)}` : ''}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

