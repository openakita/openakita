import { useState, useEffect, useCallback, useRef } from 'react';
import { PhaserGame, type GameRef } from '../components/pixel-office/PhaserGame';
import { PixelOfficeEventLog, type EventLogEntry } from '../components/pixel-office/PixelOfficeEventLog';
import { PixelOfficeAgentList, type AgentListItem } from '../components/pixel-office/PixelOfficeAgentList';
import { PixelOfficeThemeSelector } from '../components/pixel-office/PixelOfficeThemeSelector';
import { EventBus } from '../components/pixel-office/EventBus';
import type { OrgData } from '../components/pixel-office/OfficeScene';
import type { AgentSpriteConfig } from '../components/pixel-office/AgentSprite';
import { safeFetch } from '../providers';
import '../components/pixel-office/pixel-office.css';

interface AgentDetailPanel {
  nodeId: string;
  config: AgentSpriteConfig;
  x: number;
  y: number;
}

interface AgentContextMenu {
  nodeId: string;
  config: AgentSpriteConfig;
  x: number;
  y: number;
}

const MAX_LOG_ENTRIES = 200;
const POLL_INTERVAL = 5000;
const SOLO_ID = '__solo__';
const SOLO_ORG_DATA: OrgData = { orgId: SOLO_ID, nodes: [], agentProfiles: {} };

function readStoredOrgId(): string {
  try { return localStorage.getItem('po_selected_org') ?? ''; } catch { return ''; }
}
function writeStoredOrgId(id: string) {
  try { localStorage.setItem('po_selected_org', id); } catch { /* */ }
}

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
  const [selectedOrgId, _setSelectedOrgId] = useState<string>(readStoredOrgId);
  const [panelOpen, setPanelOpen] = useState(() => {
    try { return localStorage.getItem('po_panel_open') !== 'false'; } catch { return true; }
  });
  const [orgDropdownOpen, setOrgDropdownOpen] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);
  const gameRef = useRef<GameRef>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const selectedOrgIdRef = useRef(selectedOrgId);

  const setSelectedOrgId = useCallback((id: string) => {
    selectedOrgIdRef.current = id;
    writeStoredOrgId(id);
    _setSelectedOrgId(id);
  }, []);

  const isSoloMode = selectedOrgId === SOLO_ID || (!selectedOrgId && orgList.length === 0);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await safeFetch(`${apiBaseUrl}/api/orgs`);
        if (resp.ok && !cancelled) {
          const data = await resp.json();
          const orgs = (data.organizations ?? data) as Array<{ id: string; name: string }>;
          setOrgList(orgs);
          const cur = selectedOrgIdRef.current;
          if (orgs.length > 0 && !cur) {
            setSelectedOrgId(orgs[0].id);
          }
        }
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBaseUrl, visible]);

  useEffect(() => {
    if (!visible || !selectedOrgId || isSoloMode) {
      if (isSoloMode) { setOrgData(null); setAgents([]); }
      return;
    }
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
  }, [apiBaseUrl, visible, selectedOrgId, isSoloMode]);

  useEffect(() => {
    if (!visible || !selectedOrgId || isSoloMode) return;
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
  }, [apiBaseUrl, visible, selectedOrgId, isSoloMode]);

  const handleEventLog = useCallback((entry: unknown) => {
    setEventLog(prev => {
      const next = [...prev, entry as EventLogEntry];
      return next.length > MAX_LOG_ENTRIES ? next.slice(-MAX_LOG_ENTRIES) : next;
    });
  }, []);

  // Agent click/context menu state
  const [agentDetail, setAgentDetail] = useState<AgentDetailPanel | null>(null);
  const [agentCtxMenu, setAgentCtxMenu] = useState<AgentContextMenu | null>(null);

  useEffect(() => {
    const onClickAgent = (nodeId: string, config: AgentSpriteConfig, sx: number, sy: number) => {
      setAgentCtxMenu(null);
      setAgentDetail({ nodeId, config, x: sx, y: sy });
    };
    const onCtxAgent = (nodeId: string, config: AgentSpriteConfig, sx: number, sy: number) => {
      setAgentDetail(null);
      setAgentCtxMenu({ nodeId, config, x: sx, y: sy });
    };
    EventBus.on('agent-clicked', onClickAgent);
    EventBus.on('agent-context-menu', onCtxAgent);
    return () => {
      EventBus.off('agent-clicked', onClickAgent);
      EventBus.off('agent-context-menu', onCtxAgent);
    };
  }, []);

  const handleAssignTask = useCallback((nodeId: string) => {
    const task = prompt(`分配任务给 ${nodeId}：`);
    if (!task) return;
    safeFetch(`${apiBaseUrl}/api/orgs/${selectedOrgId}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: task, target_node_id: nodeId }),
    }).catch(() => {});
    setAgentCtxMenu(null);
  }, [apiBaseUrl, selectedOrgId]);

  const effectiveOrgData = isSoloMode ? SOLO_ORG_DATA : (orgData ?? null);

  if (!visible) return null;

  const selectedOrg = orgList.find(o => o.id === selectedOrgId);

  return (
    <div className="poRoot">
      {/* Compact header */}
      <header className="poHeader">
        <div className="poHeaderLeft">
          {isSoloMode ? (
            <h2 className="poOrgName">个人工作室</h2>
          ) : selectedOrg ? (
            <>
              <h2 className="poOrgName">{selectedOrg.name || selectedOrg.id}</h2>
              {orgData && <span className="poNodeCount">{orgData.nodes.length} 节点</span>}
            </>
          ) : (
            <h2 className="poOrgName">像素办公室</h2>
          )}
          <div className="poOrgSwitcher">
            <button
              className="poOrgSwitchBtn"
              onClick={() => setOrgDropdownOpen(!orgDropdownOpen)}
              title="切换模式"
            >
              <svg width="10" height="6" viewBox="0 0 10 6" fill="currentColor">
                <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            {orgDropdownOpen && (
              <>
                <div className="poOrgBackdrop" onClick={() => setOrgDropdownOpen(false)} />
                <div className="poOrgDropdown">
                  <button
                    className={`poOrgDropItem${isSoloMode ? ' active' : ''}`}
                    onClick={() => { setSelectedOrgId(SOLO_ID); setDataVersion(v => v + 1); setOrgDropdownOpen(false); }}
                  >
                    🐕 个人工作室
                  </button>
                  {orgList.length > 0 && <div className="poOrgDropDivider" />}
                  {orgList.map(o => (
                    <button
                      key={o.id}
                      className={`poOrgDropItem${o.id === selectedOrgId ? ' active' : ''}`}
                      onClick={() => { setSelectedOrgId(o.id); setDataVersion(v => v + 1); setOrgDropdownOpen(false); }}
                    >
                      {o.name || o.id}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
        <div className="poHeaderRight">
          {!isSoloMode && !orgData && selectedOrgId && <span className="poHeaderInfo">加载中…</span>}
        </div>
      </header>

      {/* Canvas */}
      <div className="poCanvas">
        <PhaserGame
          ref={gameRef}
          themeId={themeId}
          orgData={effectiveOrgData}
          dataVersion={dataVersion}
          onEventLog={handleEventLog}
        />
        {!effectiveOrgData && selectedOrgId && (
          <div className="poCanvasOverlay">
            <div className="poCanvasLoading">加载组织数据…</div>
          </div>
        )}
      </div>

      {/* Fold toggle */}
      <button className="poFoldToggle" onClick={() => setPanelOpen(p => {
        const next = !p;
        try { localStorage.setItem('po_panel_open', String(next)); } catch { /* */ }
        return next;
      })}>
        {panelOpen ? '▼ 收起面板' : '▲ 展开面板'}
      </button>

      {/* Bottom bar */}
      {panelOpen && (
        <div className="poBottom">
          <PixelOfficeEventLog entries={eventLog} />
          <PixelOfficeAgentList
            agents={agents}
            onAgentClick={(nodeId) => EventBus.emit('zoom-to-node', nodeId)}
          />
          <PixelOfficeThemeSelector
            currentThemeId={themeId}
            onSelectTheme={setThemeId}
          />
        </div>
      )}

      {/* Agent detail panel (click) */}
      {agentDetail && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 900 }} onClick={() => setAgentDetail(null)} />
          <div style={{
            position: 'fixed', left: Math.min(agentDetail.x, window.innerWidth - 260),
            top: Math.min(agentDetail.y + 10, window.innerHeight - 200),
            width: 240, zIndex: 901,
            background: 'var(--card, #1a1a2e)', border: '1px solid var(--border, rgba(255,255,255,0.12))',
            borderRadius: 12, padding: 14, boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            color: 'var(--text, #e0e0e0)', fontSize: 13,
          }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 8 }}>
              {agentDetail.config.icon && <span style={{ marginRight: 4 }}>{agentDetail.config.icon}</span>}
              {agentDetail.config.name}
            </div>
            <div style={{ opacity: 0.7, marginBottom: 4 }}>ID: {agentDetail.nodeId}</div>
            {agentDetail.config.department && <div style={{ opacity: 0.7, marginBottom: 4 }}>部门: {agentDetail.config.department}</div>}
            <div style={{ opacity: 0.7, marginBottom: 8 }}>状态: {agentDetail.config.status || 'idle'}</div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={() => { handleAssignTask(agentDetail.nodeId); setAgentDetail(null); }}
                style={{
                  flex: 1, padding: '5px 0', borderRadius: 6, border: 'none',
                  background: 'var(--brand, #3b82f6)', color: '#fff', fontSize: 12, cursor: 'pointer',
                }}
              >
                分配任务
              </button>
              <button
                onClick={() => { EventBus.emit('zoom-to-node', agentDetail.nodeId); setAgentDetail(null); }}
                style={{
                  flex: 1, padding: '5px 0', borderRadius: 6, border: '1px solid var(--border, rgba(255,255,255,0.2))',
                  background: 'transparent', color: 'var(--text)', fontSize: 12, cursor: 'pointer',
                }}
              >
                聚焦
              </button>
            </div>
          </div>
        </>
      )}

      {/* Agent context menu (right-click) */}
      {agentCtxMenu && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 900 }} onClick={() => setAgentCtxMenu(null)} />
          <div style={{
            position: 'fixed', left: agentCtxMenu.x, top: agentCtxMenu.y,
            zIndex: 901, minWidth: 140,
            background: 'var(--card, #1a1a2e)', border: '1px solid var(--border, rgba(255,255,255,0.12))',
            borderRadius: 8, padding: '4px 0', boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
            color: 'var(--text, #e0e0e0)', fontSize: 13,
          }}>
            {[
              { label: '📋 分配任务', action: () => handleAssignTask(agentCtxMenu.nodeId) },
              { label: '🔍 聚焦', action: () => { EventBus.emit('zoom-to-node', agentCtxMenu.nodeId); setAgentCtxMenu(null); } },
              { label: '📄 查看详情', action: () => { setAgentDetail({ ...agentCtxMenu }); setAgentCtxMenu(null); } },
            ].map((item) => (
              <button
                key={item.label}
                onClick={item.action}
                style={{
                  display: 'block', width: '100%', padding: '6px 14px', border: 'none',
                  background: 'transparent', color: 'inherit', textAlign: 'left',
                  cursor: 'pointer', fontSize: 13,
                }}
                onMouseEnter={(e) => { (e.target as HTMLButtonElement).style.background = 'rgba(255,255,255,0.08)'; }}
                onMouseLeave={(e) => { (e.target as HTMLButtonElement).style.background = 'transparent'; }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
