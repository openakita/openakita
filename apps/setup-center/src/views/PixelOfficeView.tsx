import { useState, useEffect, useCallback, useRef } from 'react';
import { PhaserGame, type GameRef } from '../components/pixel-office/PhaserGame';
import { PixelOfficeEventLog, type EventLogEntry } from '../components/pixel-office/PixelOfficeEventLog';
import { PixelOfficeAgentList, type AgentListItem } from '../components/pixel-office/PixelOfficeAgentList';
import { PixelOfficeThemeSelector } from '../components/pixel-office/PixelOfficeThemeSelector';
import { EventBus } from '../components/pixel-office/EventBus';
import type { OrgData } from '../components/pixel-office/OfficeScene';
import { safeFetch } from '../providers';
import '../components/pixel-office/pixel-office.css';

const MAX_LOG_ENTRIES = 200;
const POLL_INTERVAL = 5000;

export function PixelOfficeView({
  apiBaseUrl = 'http://127.0.0.1:18900',
  visible = true,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
}) {
  const [themeId, setThemeId] = useState('office');
  const [orgData, setOrgData] = useState<OrgData | null>(null);
  const [agents, setAgents] = useState<AgentListItem[]>([]);
  const [eventLog, setEventLog] = useState<EventLogEntry[]>([]);
  const [orgList, setOrgList] = useState<Array<{ id: string; name: string }>>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string>('');
  const gameRef = useRef<GameRef>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!visible) return;
    (async () => {
      try {
        const resp = await safeFetch(`${apiBaseUrl}/api/orgs`);
        if (resp.ok) {
          const data = await resp.json();
          const orgs = (data.organizations ?? data) as Array<{ id: string; name: string }>;
          setOrgList(orgs);
          if (orgs.length > 0 && !selectedOrgId) {
            setSelectedOrgId(orgs[0].id);
          }
        }
      } catch { /* ignore */ }
    })();
  }, [apiBaseUrl, visible, selectedOrgId]);

  useEffect(() => {
    if (!visible || !selectedOrgId) return;
    let mounted = true;

    const fetchOrgData = async () => {
      try {
        const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${selectedOrgId}`);
        if (!resp.ok || !mounted) return;
        const org = await resp.json();

        const profilesResp = await safeFetch(`${apiBaseUrl}/api/agents/profiles`);
        const profilesData = profilesResp.ok ? await profilesResp.json() : {};
        const profiles: Record<string, unknown> = profilesData.profiles ?? profilesData ?? {};

        const profileMap: OrgData['agentProfiles'] = {};
        const agentList: AgentListItem[] = [];

        for (const node of org.nodes ?? []) {
          const pid = node.agent_profile_id || node.id;
          const p = (profiles as Record<string, Record<string, unknown>>)[pid];
          profileMap[pid] = {
            name: (p?.name as string) ?? node.role_title ?? node.id,
            color: (p?.color as string) ?? '#4A90D9',
            icon: (p?.icon as string) ?? undefined,
            pixel_appearance: (p?.pixel_appearance as Record<string, unknown>) ?? null,
          };
          agentList.push({
            nodeId: node.id,
            name: profileMap[pid].name,
            color: profileMap[pid].color,
            icon: profileMap[pid].icon,
            status: node.status ?? 'idle',
            department: node.department ?? '',
            pixelAppearance: profileMap[pid].pixel_appearance,
          });
        }

        const data: OrgData = {
          orgId: selectedOrgId,
          nodes: org.nodes ?? [],
          agentProfiles: profileMap,
        };

        if (mounted) {
          setOrgData(data);
          setAgents(agentList);
        }
      } catch { /* ignore */ }
    };

    fetchOrgData();
    const interval = setInterval(fetchOrgData, POLL_INTERVAL);
    return () => { mounted = false; clearInterval(interval); };
  }, [apiBaseUrl, visible, selectedOrgId]);

  useEffect(() => {
    if (!visible || !selectedOrgId) return;
    const wsBase = apiBaseUrl.replace(/^http/, 'ws');
    const wsUrl = `${wsBase}/ws/org/${selectedOrgId}`;

    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch {
      return;
    }
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        const eventType = msg.type ?? msg.event;
        if (eventType?.startsWith('org:')) {
          EventBus.emit('org-event', eventType, msg.payload ?? msg.data ?? msg);
        }
      } catch { /* ignore */ }
    };

    ws.onerror = () => {};
    ws.onclose = () => {};

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [apiBaseUrl, visible, selectedOrgId]);

  const handleEventLog = useCallback((entry: unknown) => {
    setEventLog(prev => {
      const next = [...prev, entry as EventLogEntry];
      return next.length > MAX_LOG_ENTRIES ? next.slice(-MAX_LOG_ENTRIES) : next;
    });
  }, []);

  if (!visible) return null;

  const selectedOrg = orgList.find(o => o.id === selectedOrgId);

  return (
    <div className="poRoot">
      {/* Header */}
      <header className="poHeader">
        <h2 className="poTitle">像素办公室</h2>

        {orgList.length > 0 && (
          <select
            className="poOrgSelect"
            value={selectedOrgId}
            onChange={e => setSelectedOrgId(e.target.value)}
          >
            {orgList.map(o => (
              <option key={o.id} value={o.id}>{o.name || o.id}</option>
            ))}
          </select>
        )}

        <div style={{ flex: 1 }} />

        {orgData && (
          <span className="poHeaderInfo">{orgData.nodes.length} 个节点</span>
        )}
        {!orgData && selectedOrgId && (
          <span className="poHeaderInfo">加载中…</span>
        )}
        {!selectedOrgId && (
          <span className="poHeaderInfo">请先创建一个组织</span>
        )}
      </header>

      {/* Canvas */}
      <div className="poCanvas">
        <PhaserGame
          ref={gameRef}
          themeId={themeId}
          orgData={orgData}
          onEventLog={handleEventLog}
        />
        {!orgData && selectedOrgId && (
          <div className="poCanvasOverlay">
            <div className="poCanvasLoading">加载组织数据…</div>
          </div>
        )}
      </div>

      {/* Bottom bar */}
      <div className="poBottom">
        <PixelOfficeEventLog entries={eventLog} />
        <PixelOfficeAgentList agents={agents} />
        <PixelOfficeThemeSelector
          currentThemeId={themeId}
          onSelectTheme={setThemeId}
        />
      </div>
    </div>
  );
}
