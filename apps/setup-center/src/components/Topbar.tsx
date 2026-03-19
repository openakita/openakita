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
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuTrigger, DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { LogOut } from "lucide-react";
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
  onSetTheme: (theme: Theme) => void;
  themePrefState: Theme;
  isWeb?: boolean;
  onLogout?: () => void;
  webAccessUrl?: string;
  apiBaseUrl?: string;
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
  toggleTheme, themePrefState, isWeb, onLogout, webAccessUrl, apiBaseUrl,
  onToggleMobileSidebar, serverName, onServerManager,
}: TopbarProps) {
  const { t, i18n } = useTranslation();
  const [remoteCopyState, setRemoteCopyState] = useState<"idle" | "copied" | "no_ip">("idle");

  const copyRemoteUrl = async () => {
    const base = apiBaseUrl || "http://127.0.0.1:18900";
    try {
      let data: any;
      for (const url of [base, "http://127.0.0.1:18900"]) {
        try {
          const res = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(3000) });
          if (res.ok) { data = await res.json(); break; }
        } catch { /* try next */ }
      }
      const ip = data?.local_ip;
      if (ip && ip !== "127.0.0.1" && ip !== "::1" && ip !== "localhost") {
        const port = new URL(base).port || "18900";
        await navigator.clipboard.writeText(`http://${ip}:${port}/web`);
        setRemoteCopyState("copied");
        setTimeout(() => setRemoteCopyState("idle"), 2000);
      } else {
        setRemoteCopyState("no_ip");
        setTimeout(() => setRemoteCopyState("idle"), 4000);
      }
    } catch {
      setRemoteCopyState("no_ip");
      setTimeout(() => setRemoteCopyState("idle"), 4000);
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
                color: remoteCopyState === "copied" ? "var(--ok, #10b981)"
                  : remoteCopyState === "no_ip" ? "var(--warning-text, #92400e)"
                  : "var(--accent, #5B8DEF)",
                opacity: remoteCopyState !== "idle" ? 1 : 0.7,
                transition: "color 0.2s",
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.opacity = "1"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.opacity = remoteCopyState !== "idle" ? "1" : "0.7"; }}
            >
              <IconClipboard size={11} />
              <span>{
                remoteCopyState === "copied" ? t("common.copied", "已复制")
                : remoteCopyState === "no_ip" ? t("topbar.ipNotFound", "请用 ipconfig 或 ifconfig 查看本机 IP")
                : t("topbar.remoteUrl", "远程地址")
              }</span>
            </span>
          </>
        )}
        <span className="topbarEpCount">{t("topbar.endpoints", { count: endpointCount })}</span>
        {dataMode === "remote" && <span className="pill" style={{ fontSize: 10, marginLeft: 4, background: "#e3f2fd", color: "#1565c0" }}>{t("connect.remoteMode")}</span>}
      </div>
      <TooltipProvider delayDuration={300}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          {isWeb ? (
            onLogout && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon-sm" onClick={onLogout}>
                    <LogOut size={16} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t("topbar.logout")}</TooltipContent>
              </Tooltip>
            )
          ) : serviceRunning ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-sm" onClick={onDisconnect} disabled={!!busy}>
                  <LogOut size={16} />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">{t("topbar.disconnect")}</TooltipContent>
            </Tooltip>
          ) : (
            <>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="outline" size="sm" onClick={onConnect} disabled={!!busy}>
                    <IconLink size={14} />
                    <span>{t("topbar.connect")}</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t("topbar.connect")}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="outline" size="sm" onClick={onStart} disabled={!!busy}>
                    <IconPower size={14} />
                    <span>{t("topbar.start")}</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t("topbar.start")}</TooltipContent>
              </Tooltip>
            </>
          )}

          <div className="h-4 w-px bg-border" />

          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-sm" onClick={onRefreshAll} disabled={!!busy}>
                <IconRefresh size={16} />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t("topbar.refresh")}</TooltipContent>
          </Tooltip>

          <div className="h-4 w-px bg-border" />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" title={t("topbar.themeLabel", "主题")}>
                {themePrefState === "system" ? <IconLaptop size={16} /> : themePrefState === "dark" ? <IconMoon size={16} /> : <IconSun size={16} />}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[140px]">
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">{t("topbar.themeLabel", "主题")}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuRadioGroup value={themePrefState} onValueChange={(v) => onSetTheme(v as Theme)}>
                <DropdownMenuRadioItem value="system" className="gap-2">
                  <IconLaptop size={14} />
                  {t("topbar.themeSystem")}
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="light" className="gap-2">
                  <IconSun size={14} />
                  {t("topbar.themeLight")}
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="dark" className="gap-2">
                  <IconMoon size={14} />
                  {t("topbar.themeDark")}
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>

          <div className="h-4 w-px bg-border" />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" title={t("topbar.langLabel", "语言")}>
                <IconGlobe size={16} />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[140px]">
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">{t("topbar.langLabel", "语言")}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuRadioGroup value={i18n.language?.startsWith("zh") ? "zh" : "en"} onValueChange={(v) => i18n.changeLanguage(v)}>
                <DropdownMenuRadioItem value="zh">中文</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="en">English</DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </TooltipProvider>
    </div>
  );
}
