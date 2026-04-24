import { PixelAvatar } from '../pixel-avatar';

const STATUS_COLOR: Record<string, string> = {
  idle: '#95a5a6',
  busy: '#27ae60',
  waiting: '#f39c12',
  error: '#e74c3c',
  offline: '#555',
  frozen: '#3498db',
};

const STATUS_LABEL: Record<string, string> = {
  idle: '空闲',
  busy: '忙碌',
  waiting: '等待',
  error: '错误',
  offline: '离线',
  frozen: '冻结',
};

export interface AgentListItem {
  nodeId: string;
  name: string;
  color: string;
  icon?: string;
  status: string;
  department: string;
  pixelAppearance?: Record<string, unknown> | null;
}

export function PixelOfficeAgentList({
  agents,
  onAgentClick,
}: {
  agents: AgentListItem[];
  onAgentClick?: (nodeId: string) => void;
}) {
  return (
    <div className="poPanel">
      <div className="poPanelHeader">成员 ({agents.length})</div>
      <div className="poPanelBody">
        {agents.length === 0 && <div className="poEmpty">暂无成员</div>}
        {agents.map(a => (
          <div
            key={a.nodeId}
            className="poAgentRow"
            onClick={() => onAgentClick?.(a.nodeId)}
          >
            <PixelAvatar
              agentId={a.nodeId}
              profileColor={a.color}
              profileIcon={a.icon}
              profileName={a.name}
              appearance={a.pixelAppearance as never}
              size={24}
            />
            <div className="poAgentInfo">
              <span className="poAgentName">{a.name}</span>
              {a.department && <span className="poAgentDept">{a.department}</span>}
            </div>
            <span className="poAgentStatus" style={{ color: STATUS_COLOR[a.status] ?? '#888' }}>
              <span className="poStatusDot" style={{ background: STATUS_COLOR[a.status] ?? '#888' }} />
              {STATUS_LABEL[a.status] ?? a.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

