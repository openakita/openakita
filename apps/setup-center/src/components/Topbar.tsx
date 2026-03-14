import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { WorkspaceSummary } from "../types";
import type { Theme } from "../theme";
import {
  DotGreen, DotGray,
  IconX, IconLink, IconPower, IconRefresh,
  IconLaptop, IconMoon, IconSun, IconGlobe, IconClipboard,
} from "../icons";
import { Button } from "@/components/ui/button";
import { openExternalUrl } from "../platform";
import { copyToClipboard } from "../utils/clipboard";

export type TopbarProps = {
  wsDropdownOpen: boolean;
  setWsDropdownOpen: (v: boolean | ((prev: boolean) => boolean)) => void;
  currentWorkspaceId: string | null;
  workspaces: WorkspaceSummary[];
  onSwitchWorkspace: (id: string) => Promise<void>;
  wsQuickCreateOpen: boolean;
  setWsQuickCreateOpen: (v: boolean) => void;
  wsQuickName: string;
  setWsQuickName: (v: string) => void;
  onCreateWorkspace: (id: string, name: string) => Promise<void>;
  serviceRunning: boolean;
  endpointCount: number;
  dataMode: "local" | "remote";
  busy: string | null;
  onDisconnect: () => void;
  onConnect: () => void;
  onStart: () => Promise<void>;
  onRefreshAll: () => Promise<void>;
  toggleTheme: () => void;
  themePrefState: Theme;
  isWeb?: boolean;
  onLogout?: () => void;
  webAccessUrl?: string;
  onToggleMobileSidebar?: () => void;
  serverName?: string;
  onServerManager?: () => void;
};

export function Topbar({
  wsDropdownOpen, setWsDropdownOpen,
  currentWorkspaceId, workspaces,
  onSwitchWorkspace,
  wsQuickCreateOpen, setWsQuickCreateOpen,
  wsQuickName, setWsQuickName,
  onCreateWorkspace,
  serviceRunning, endpointCount, dataMode, busy,
  onDisconnect, onConnect, onStart, onRefreshAll,
  toggleTheme, themePrefState, isWeb, onLogout, webAccessUrl, onToggleMobileSidebar,
  serverName, onServerManager,
}: TopbarProps) {
  const { t, i18n } = useTranslation();
  const [remoteCopied, setRemoteCopied] = useState(false);

  const copyRemoteUrl = async () => {
    try {
      const res = await fetch("/api/health", { signal: AbortSignal.timeout(3000) }).catch(() =>
        fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(3000) })
      );
      const data = await res.json();
      const ip = data.local_ip || "127.0.0.1";
      const port = location.port || "18900";
      const url = `http://${ip}:${port}/web`;
      const ok = await copyToClipboard(url);
      if (ok) {
        setRemoteCopied(true);
        setTimeout(() => setRemoteCopied(false), 2000);
      }
    } catch {
      const url = `http://127.0.0.1:18900/web`;
      const ok = await copyToClipboard(url);
      if (ok) {
        setRemoteCopied(true);
        setTimeout(() => setRemoteCopied(false), 2000);
      }
    }
  };

  return (
    <div className="topbar">
      <div className="topbarStatusRow">
        {onToggleMobileSidebar && (
          <button className="topbarHamburger mobileOnly" onClick={onToggleMobileSidebar} aria-label="Menu">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
          </button>
        )}
        {onServerManager && serverName && (
          <button
            className="topbarServerBtn"
            onClick={onServerManager}
            title={t("server.switchServer", { defaultValue: "切换服务器" })}
          >
            {serverName}
          </button>
        )}
        {/* Workspace quick switcher */}
        <span className="topbarWs" style={{ position: "relative", cursor: "pointer", userSelect: "none" }}>
          <span
            onClick={() => setWsDropdownOpen((v: boolean) => !v)}
            title={t("topbar.switchWorkspace")}
            style={{ display: "inline-flex", alignItems: "center", gap: 3 }}
          >
            {currentWorkspaceId || "default"}
            <span style={{ fontSize: 8, opacity: 0.6 }}>▾</span>
          </span>
          {wsDropdownOpen && (
            <div
              style={{
                position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 999,
                background: "var(--card-bg, #fff)", color: "var(--text)", border: "1px solid var(--line)", borderRadius: 8,
                boxShadow: "var(--shadow)", minWidth: 220, padding: "6px 0",
              }}
              onMouseLeave={() => setWsDropdownOpen(false)}
            >
              {workspaces.length === 0 && (
                <div style={{ padding: "8px 14px", fontSize: 12, opacity: 0.5 }}>{t("topbar.noWorkspaces")}</div>
              )}
              {workspaces.map((w) => (
                <div
                  key={w.id}
                  style={{
                    padding: "7px 14px", cursor: "pointer", fontSize: 13,
                    background: w.isCurrent ? "rgba(37,99,235,0.08)" : "transparent",
                    fontWeight: w.isCurrent ? 700 : 400,
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(37,99,235,0.12)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = w.isCurrent ? "rgba(37,99,235,0.08)" : "transparent"; }}
                  onClick={async () => {
                    if (w.isCurrent) { setWsDropdownOpen(false); return; }
                    setWsDropdownOpen(false);
                    await onSwitchWorkspace(w.id);
                  }}
                >
                  <span>{w.name} <span style={{ opacity: 0.5, fontSize: 11 }}>({w.id})</span></span>
                  {w.isCurrent && <span style={{ color: "var(--brand)", fontSize: 11 }}>✓</span>}
                </div>
              ))}
              <div style={{ borderTop: "1px solid var(--line)", margin: "4px 0" }} />
              {!wsQuickCreateOpen ? (
                <div
                  style={{ padding: "7px 14px", cursor: "pointer", fontSize: 12, color: "var(--brand)", fontWeight: 600 }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(37,99,235,0.08)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                  onClick={() => { setWsQuickCreateOpen(true); setWsQuickName(""); }}
                >
                  + {t("topbar.quickCreateWs")}
                </div>
              ) : (
                <div style={{ padding: "6px 12px" }}>
                  <input
                    autoFocus
                    style={{ width: "100%", fontSize: 12, marginBottom: 6 }}
                    value={wsQuickName}
                    onChange={(e) => setWsQuickName(e.target.value)}
                    placeholder={t("topbar.quickCreateWsPlaceholder")}
                    onKeyDown={async (e) => {
                      if (e.key === "Enter" && wsQuickName.trim()) {
                        const raw = wsQuickName.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_").replace(/^_+|_+$/g, "").slice(0, 32);
                        const id = raw && /[a-z0-9]/.test(raw) ? raw : `ws_${Date.now()}`;
                        await onCreateWorkspace(id, wsQuickName.trim());
                        setWsQuickCreateOpen(false);
                        setWsDropdownOpen(false);
                      } else if (e.key === "Escape") {
                        setWsQuickCreateOpen(false);
                      }
                    }}
                  />
                  <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                    <button style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setWsQuickCreateOpen(false)}>
                      {t("topbar.quickCreateWsCancel")}
                    </button>
                    <button
                      className="btnPrimary"
                      style={{ fontSize: 11, padding: "2px 8px" }}
                      disabled={!wsQuickName.trim()}
                      onClick={async () => {
                        const name = wsQuickName.trim();
                        const rawId = name.toLowerCase().replace(/[^a-z0-9_-]/g, "_").replace(/^_+|_+$/g, "").slice(0, 32);
                        const id = rawId && /[a-z0-9]/.test(rawId) ? rawId : `ws_${Date.now()}`;
                        await onCreateWorkspace(id, name);
                        setWsQuickCreateOpen(false);
                        setWsDropdownOpen(false);
                      }}
                    >
                      {t("topbar.quickCreateWsOk")}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </span>
        <span className="topbarIndicator">
          {serviceRunning ? <DotGreen /> : <DotGray />}
          <span>{serviceRunning ? t("topbar.running") : t("topbar.stopped")}</span>
        </span>
        {webAccessUrl && serviceRunning && !isWeb && (
          <>
            <span
              className="topbarWebAccess"
              onClick={() => openExternalUrl(webAccessUrl)}
              title={webAccessUrl}
              style={{
                cursor: "pointer", fontSize: 11, display: "inline-flex", alignItems: "center", gap: 3,
                color: "var(--accent, #5B8DEF)", opacity: 0.85,
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.opacity = "1"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.opacity = "0.85"; }}
            >
              <IconGlobe size={11} />
              <span style={{ textDecoration: "underline" }}>{t("topbar.webAccess")}</span>
            </span>
            <span
              onClick={copyRemoteUrl}
              title={t("topbar.copyRemoteUrl", "复制远程访问地址")}
              style={{
                cursor: "pointer", fontSize: 11, display: "inline-flex", alignItems: "center", gap: 2,
                color: remoteCopied ? "var(--ok, #10b981)" : "var(--accent, #5B8DEF)",
                opacity: remoteCopied ? 1 : 0.7,
                transition: "color 0.2s",
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.opacity = "1"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.opacity = remoteCopied ? "1" : "0.7"; }}
            >
              <IconClipboard size={11} />
              <span>{remoteCopied ? t("common.copied", "已复制") : t("topbar.remoteUrl", "远程地址")}</span>
            </span>
          </>
        )}
        <span className="topbarEpCount">{t("topbar.endpoints", { count: endpointCount })}</span>
        {dataMode === "remote" && <span className="pill" style={{ fontSize: 10, marginLeft: 4, background: "#e3f2fd", color: "#1565c0" }}>{t("connect.remoteMode")}</span>}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
        {isWeb ? (
          onLogout && (
            <Button variant="destructive" size="xs" onClick={onLogout} title={t("topbar.logout")}>
              <IconX size={13} />
              <span>{t("topbar.logout")}</span>
            </Button>
          )
        ) : serviceRunning ? (
          <Button variant="secondary" size="xs" onClick={onDisconnect} disabled={!!busy} title={t("topbar.disconnect")}>
            <IconX size={13} />
            <span>{t("topbar.disconnect")}</span>
          </Button>
        ) : (
          <>
            <Button variant="outline" size="xs" onClick={onConnect} disabled={!!busy} title={t("topbar.connect")}>
              <IconLink size={13} />
              <span>{t("topbar.connect")}</span>
            </Button>
            <Button variant="outline" size="xs" onClick={onStart} disabled={!!busy} title={t("topbar.start")}>
              <IconPower size={13} />
              <span>{t("topbar.start")}</span>
            </Button>
          </>
        )}
        <Button variant="ghost" size="icon-xs" onClick={onRefreshAll} disabled={!!busy} title={t("topbar.refresh")}>
          <IconRefresh size={14} />
        </Button>
        <Button variant="ghost" size="icon-xs" onClick={toggleTheme} title={themePrefState === "system" ? "主题: 随系统" : themePrefState === "dark" ? "主题: 暗色" : "主题: 亮色"}>
          {themePrefState === "system" ? <IconLaptop size={14} /> : themePrefState === "dark" ? <IconMoon size={14} /> : <IconSun size={14} />}
        </Button>
        <Button variant="ghost" size="icon-xs" onClick={() => { i18n.changeLanguage(i18n.language?.startsWith("zh") ? "en" : "zh"); }} title="中/EN">
          <IconGlobe size={14} />
        </Button>
      </div>
    </div>
  );
}
