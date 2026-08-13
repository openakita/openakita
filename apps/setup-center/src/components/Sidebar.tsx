import { Fragment, useState, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { LogOut } from "lucide-react";
import { toast } from "sonner";
import type { StepId, Step, ViewId, PluginUIApp } from "../types";
import {
  IconChat, IconIM, IconSkills, IconStatus, IconConfig,
  IconChevronDown, IconChevronRight,
  IconZap, IconPlug, IconCalendar,
  IconBug, IconBrain, IconUsers, IconBot,
  IconGear, IconBook, IconStorefront, IconPuzzle, IconFingerprint, IconLayoutGrid,
  IconShield, IconRadar, IconBuilding, IconBarChart, IconRefresh, IconHelp,
  IconAlertCircle,
} from "../icons";
import logoUrl from "../assets/logo.png";
import {
  ACCOUNT_STATUS_CHANGED_EVENT,
  type AccountStatusSummary,
} from "../utils/accountStatusEvents";
import {
  connectOpenAkitaAccount,
  disconnectOpenAkitaAccount,
  loadAccountCapability,
  refreshOpenAkitaAccountEntitlements,
  type AccountCapability,
} from "../utils/accountLogin";

export type SidebarProps = {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  view: ViewId;
  onViewChange: (v: ViewId) => void;
  configMode: boolean;
  onEnterConfig: () => void;
  onExitConfig: () => void;
  steps: Step[];
  stepId: StepId;
  onStepChange: (id: StepId) => void;
  disabledViews: string[];
  storeVisible: boolean;
  serviceRunning: boolean;
  onRefreshStatus: () => Promise<void>;
  mobileOpen?: boolean;
  httpApiBase?: string;
  unreadFeedbackCount?: number;
  pendingApprovalsCount?: number;
  onCheckForUpdate?: () => Promise<void>;
  updateCheckPending?: boolean;
};

const stepIcons: Partial<Record<StepId, React.ReactNode>> = {
  llm: <IconZap size={14} />,
  im: <IconIM size={14} />,
  tools: <IconSkills size={14} />,
  agent: <IconBot size={14} />,
  workspace: <IconBook size={14} />,
  advanced: <IconGear size={14} />,
};

type NavGroupId = "capabilities" | "apps" | "monitor" | "multiAgent" | "store";
const GROUP_ICON_SIZE = 16;
const CAPABILITY_VIEWS: ViewId[] = ["skills", "mcp", "plugins", "memory", "scheduler"];
const MONITOR_VIEWS: ViewId[] = ["token_stats", "skill_usage", "security", "pending_approvals"];
const MULTI_AGENT_VIEWS: ViewId[] = ["dashboard", "org_editor", "pixel_office", "agent_manager"];
const STORE_VIEWS: ViewId[] = ["agent_store", "skill_store"];

const BETA_SUP = <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup>;

function NavGroupHeader({
  collapsed: sidebarCollapsed,
  icon,
  label,
  expanded,
  onToggle,
}: {
  collapsed: boolean;
  icon: React.ReactNode;
  label: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="navGroupHeader" onClick={onToggle} role="button" tabIndex={0} title={sidebarCollapsed ? label : undefined}>
      {!sidebarCollapsed ? (
        <>
          <span className="navGroupLabelWrap">
            <span className="navGroupIcon">{icon}</span>
            <span className="navGroupLabel">{label}</span>
          </span>
          <span className="navGroupChevron">
            {expanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
          </span>
        </>
      ) : (
        <span className="navGroupIcon navGroupIconCollapsed">{icon}</span>
      )}
    </div>
  );
}

export function Sidebar({
  collapsed, onToggleCollapsed,
  view, onViewChange,
  configMode, onEnterConfig, onExitConfig,
  steps, stepId, onStepChange,
  disabledViews,
  storeVisible,
  serviceRunning,
  onRefreshStatus, mobileOpen, httpApiBase,
  unreadFeedbackCount, pendingApprovalsCount,
  onCheckForUpdate, updateCheckPending = false,
}: SidebarProps) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  // Pick a localized plugin app title from `title_i18n`, falling back to the
  // default `title` string. Mirror of pickI18n() in PluginManagerView so the
  // sidebar and the manager list always show the same label per language.
  const pickAppTitle = (app: PluginUIApp): string => {
    const dict = app.title_i18n;
    if (dict && typeof dict === "object") {
      if (dict[lang]) return dict[lang];
      const base = lang.split("-")[0];
      if (base && dict[base]) return dict[base];
      if (dict.en) return dict.en;
      const first = Object.values(dict).find(v => typeof v === "string" && v);
      if (first) return first;
    }
    return app.title;
  };

  const [expandedGroups, setExpandedGroups] = useState<Record<NavGroupId, boolean>>({
    capabilities: false,
    apps: false,
    monitor: false,
    multiAgent: false,
    store: false,
  });

  const toggleGroup = useCallback((id: NavGroupId) => {
    setExpandedGroups(prev => ({ ...prev, [id]: !prev[id] }));
  }, []);

  const [pluginApps, setPluginApps] = useState<PluginUIApp[]>([]);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [accountCapability, setAccountCapability] = useState<AccountCapability | null>(null);
  const [accountSnapshot, setAccountSnapshot] = useState<AccountStatusSummary | null>(null);
  const [accountLoginPending, setAccountLoginPending] = useState(false);
  const [accountActionPending, setAccountActionPending] = useState<"refresh" | "logout" | null>(null);
  const [accountLoginError, setAccountLoginError] = useState<string | null>(null);
  const accountAreaRef = useRef<HTMLDivElement>(null);

  const refreshAccountCapability = useCallback(async () => {
    if (!httpApiBase || !serviceRunning) {
      setAccountCapability(null);
      setAccountSnapshot(null);
      return;
    }
    try {
      const capability = await loadAccountCapability(httpApiBase);
      setAccountCapability(capability);
      if (!capability.enabled) {
        setAccountSnapshot(null);
        setAccountLoginError(null);
      }
    } catch {
      setAccountCapability(null);
      setAccountSnapshot(null);
    }
  }, [httpApiBase, serviceRunning]);

  useEffect(() => {
    void refreshAccountCapability();
  }, [refreshAccountCapability]);

  const refreshAccountSnapshot = useCallback(async () => {
    if (!httpApiBase || !serviceRunning || !accountCapability?.enabled) {
      setAccountSnapshot(null);
      return;
    }
    try {
      const response = await fetch(`${httpApiBase}/api/account/status`);
      if (response.ok) setAccountSnapshot(await response.json() as AccountStatusSummary);
    } catch {
      setAccountSnapshot(null);
    }
  }, [accountCapability?.enabled, httpApiBase, serviceRunning]);

  useEffect(() => {
    void refreshAccountSnapshot();
  }, [refreshAccountSnapshot]);

  useEffect(() => {
    const onAccountStatusChanged = (event: Event) => {
      if (!accountCapability?.enabled) return;
      const snapshot = (event as CustomEvent<AccountStatusSummary>).detail;
      if (snapshot?.status) setAccountSnapshot(snapshot);
      else void refreshAccountSnapshot();
    };
    window.addEventListener(ACCOUNT_STATUS_CHANGED_EVENT, onAccountStatusChanged);
    return () => window.removeEventListener(ACCOUNT_STATUS_CHANGED_EVENT, onAccountStatusChanged);
  }, [accountCapability?.enabled, refreshAccountSnapshot]);

  useEffect(() => {
    if (!accountMenuOpen) return;
    void refreshAccountSnapshot();
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!accountAreaRef.current?.contains(event.target as Node)) setAccountMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAccountMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [accountMenuOpen, refreshAccountSnapshot]);

  // Refetch the Apps sidebar list. Triggered initially, when backend
  // availability changes, and on the global "openakita:plugin-apps-changed"
  // event dispatched by PluginManagerView after install/enable/disable/etc.
  //
  // Tauri can mark the backend process as "running" before FastAPI has mounted
  // plugin UI routes. Use sparse startup retries as a fallback; the main
  // trigger is the backend-ready event dispatched after /api/health succeeds.
  useEffect(() => {
    if (!httpApiBase || !serviceRunning) { setPluginApps([]); return; }
    let cancelled = false;
    const retryDelays = [2_000, 8_000, 20_000, 60_000, 120_000];
    const timers = new Set<ReturnType<typeof setTimeout>>();

    const clearTimers = () => {
      timers.forEach(timer => clearTimeout(timer));
      timers.clear();
    };

    const scheduleRetry = (attempt: number) => {
      const delay = retryDelays[attempt];
      if (delay == null) return false;
      const timer = setTimeout(() => {
        timers.delete(timer);
        void refetch(attempt + 1);
      }, delay);
      timers.add(timer);
      return true;
    };

    const refetch = async (attempt = 0) => {
      try {
        const r = await fetch(`${httpApiBase}/api/plugins/ui-apps`);
        const data = r.ok ? await r.json() : [];
        if (cancelled) return;
        const apps = Array.isArray(data) ? data : [];
        setPluginApps(apps);
        if (apps.length === 0) scheduleRetry(attempt);
      } catch {
        if (cancelled) return;
        if (!scheduleRetry(attempt)) setPluginApps([]);
      }
    };

    refetch();
    const onChanged = () => {
      clearTimers();
      void refetch();
    };
    window.addEventListener("openakita:plugin-apps-changed", onChanged);
    return () => {
      cancelled = true;
      clearTimers();
      window.removeEventListener("openakita:plugin-apps-changed", onChanged);
    };
  }, [httpApiBase, serviceRunning]);

  const prevViewRef = useRef(view);
  useEffect(() => {
    if (prevViewRef.current === view) return;
    prevViewRef.current = view;
    const groupOf = (v: ViewId): NavGroupId | null =>
      CAPABILITY_VIEWS.includes(v) ? "capabilities"
        : MONITOR_VIEWS.includes(v) ? "monitor"
        : MULTI_AGENT_VIEWS.includes(v) ? "multiAgent"
        : STORE_VIEWS.includes(v) ? "store"
        : (typeof v === "string" && v.startsWith("plugin_app:")) ? "apps"
        : null;
    const g = groupOf(view);
    if (g) setExpandedGroups(prev => ({ ...prev, [g]: true }));
  }, [view]);

  const capExpanded = expandedGroups.capabilities;
  const appsExpanded = expandedGroups.apps;
  const monExpanded = expandedGroups.monitor;
  const maExpanded = expandedGroups.multiAgent;
  const stExpanded = expandedGroups.store;
  const accountEnabled = accountCapability?.enabled === true;
  const accountUsesOpenAkitaBrand = accountCapability?.provider === "openakita";
  const accountProviderName = accountCapability?.display_name || t("sidebar.account");
  const accountEmail = accountSnapshot?.profile?.email;
  const accountSignedIn = Boolean(
    accountEnabled && accountSnapshot?.status && accountSnapshot.status !== "signed_out",
  );
  const accountNeedsSync = accountSignedIn && accountSnapshot?.status !== "active";
  const accountName = accountSignedIn
    ? accountSnapshot?.profile?.name
      || accountSnapshot?.profile?.preferred_username
      || accountEmail?.split("@")[0]
      || accountProviderName
    : t("sidebar.signedOut");
  const accountDetail = accountSignedIn
    ? accountEmail || accountProviderName
    : accountLoginPending
      ? t("account.waitingForAuthorization")
      : accountLoginError
        ? t("account.retrySignIn")
        : t("sidebar.connectAccount");

  const selectAccountMenuItem = (action: () => void) => {
    setAccountMenuOpen(false);
    action();
  };

  const startAccountLogin = useCallback(async () => {
    if (!accountEnabled) return;
    if (!serviceRunning || !httpApiBase) {
      toast.error(t("account.serviceRequired"));
      return;
    }

    setAccountMenuOpen(false);
    setAccountLoginPending(true);
    setAccountLoginError(null);
    const notification = toast.loading(t("account.waitingForAuthorization"));
    try {
      const snapshot = await connectOpenAkitaAccount(httpApiBase);
      setAccountSnapshot(snapshot);
      toast.success(t("account.connected"), { id: notification });
    } catch (reason) {
      const rawMessage = reason instanceof Error ? reason.message : String(reason);
      const message = rawMessage === "account_login_expired" ? t("account.loginExpired") : rawMessage;
      setAccountLoginError(message);
      toast.error(t("account.loginFailed"), { id: notification, description: message });
      setAccountMenuOpen(true);
    } finally {
      setAccountLoginPending(false);
    }
  }, [accountEnabled, httpApiBase, serviceRunning, t]);

  const refreshAccountEntitlements = useCallback(async () => {
    if (!accountEnabled || !httpApiBase) return;
    setAccountActionPending("refresh");
    const notification = toast.loading(t("account.syncingEntitlements"));
    try {
      const snapshot = await refreshOpenAkitaAccountEntitlements(httpApiBase);
      setAccountSnapshot(snapshot);
      toast.success(t("account.entitlementsSynced"), { id: notification });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      toast.error(t("account.entitlementsSyncFailed"), { id: notification, description: message });
    } finally {
      setAccountActionPending(null);
    }
  }, [accountEnabled, httpApiBase, t]);

  const logoutAccount = useCallback(async () => {
    if (!accountEnabled || !httpApiBase) return;
    setAccountActionPending("logout");
    const notification = toast.loading(t("account.signingOut"));
    try {
      const snapshot = await disconnectOpenAkitaAccount(httpApiBase);
      setAccountSnapshot(snapshot);
      setAccountLoginError(null);
      toast.success(t("account.signedOutSuccess"), { id: notification });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      toast.error(t("account.signOutFailed"), { id: notification, description: message });
    } finally {
      setAccountActionPending(null);
    }
  }, [accountEnabled, httpApiBase, t]);

  return (
    <aside className={`sidebar ${collapsed ? "sidebarCollapsed" : ""}${configMode ? " sidebarConfigMode" : ""}${mobileOpen ? " sidebarOpen" : ""}`}>
      {!configMode && <div className="sidebarHeader">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <img
            src={logoUrl}
            alt="OpenAkita"
            className="brandLogo"
            onClick={onToggleCollapsed}
            style={{ cursor: "pointer" }}
            title={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
          />
          {!collapsed && (
            <div>
              <div className="brandTitle">{t("brand.title")}</div>
              <div className="brandSub">{t("brand.sub")}</div>
            </div>
          )}
        </div>
      </div>}

      {configMode ? (
        <div className="configModeNav">
          <button type="button" className="configBackButton" onClick={onExitConfig}>
            <span className="configBackIcon"><IconChevronRight size={14} /></span>
            <span>{t("sidebar.backToApp")}</span>
          </button>
          <div className="configModeDivider" />
          <div className="configModeLabel">{t("sidebar.config")}</div>
          <div className="configModeItems">
            {steps.map((s) => {
              const isActive = view === "wizard" && s.id === stepId;
              return (
                <Fragment key={s.id}>
                  <div
                    className={`navItem configModeItem ${isActive ? "navItemActive" : ""}`}
                    onClick={() => onStepChange(s.id)}
                    role="button"
                    tabIndex={0}
                    title={s.title}
                  >
                    {stepIcons[s.id]}
                    <span>{s.title}</span>
                  </div>
                  {s.id === "agent" && (
                    <div
                      className={`navItem configModeItem ${view === "identity" ? "navItemActive" : ""}`}
                      onClick={() => onViewChange("identity")}
                      role="button"
                      tabIndex={0}
                      title={t("sidebar.identity")}
                    >
                      <IconFingerprint size={14} />
                      <span>{t("sidebar.identity")}</span>
                    </div>
                  )}
                </Fragment>
              );
            })}
          </div>
        </div>
      ) : <div className="sidebarNav">
        {/* ── Primary: always visible ── */}
        <div className={`navItem ${view === "chat" ? "navItemActive" : ""}`} onClick={() => onViewChange("chat")} role="button" tabIndex={0} title={t("sidebar.chat")}>
          <IconChat size={16} /> {!collapsed && <span>{t("sidebar.chat")}</span>}
        </div>
        {!disabledViews.includes("im") && (
          <div className={`navItem ${view === "im" ? "navItemActive" : ""}`} onClick={() => onViewChange("im")} role="button" tabIndex={0} title={t("sidebar.im")}>
            <IconIM size={16} /> {!collapsed && <span>{t("sidebar.im")}</span>}
          </div>
        )}
        <div className={`navItem ${view === "status" ? "navItemActive" : ""}`} onClick={async () => { onViewChange("status"); try { await onRefreshStatus(); } catch { /* ignore */ } }} role="button" tabIndex={0} title={t("sidebar.status")}>
          <IconStatus size={16} /> {!collapsed && <span>{t("sidebar.status")}</span>}
        </div>
        {/* ── Group: Capabilities ── */}
        <NavGroupHeader collapsed={collapsed} icon={<IconPuzzle size={GROUP_ICON_SIZE} />} label={t("sidebar.groupCapabilities")} expanded={capExpanded} onToggle={() => toggleGroup("capabilities")} />
        {(collapsed || capExpanded) && (
          <div className="navGroupItems">
            {!disabledViews.includes("skills") && (
              <div className={`navItem ${view === "skills" ? "navItemActive" : ""}`} onClick={() => onViewChange("skills")} role="button" tabIndex={0} title={t("sidebar.skills")}>
                <IconSkills size={16} /> {!collapsed && <span>{t("sidebar.skills")}</span>}
              </div>
            )}
            {!disabledViews.includes("mcp") && (
              <div className={`navItem ${view === "mcp" ? "navItemActive" : ""}`} onClick={() => onViewChange("mcp")} role="button" tabIndex={0} title="MCP">
                <IconPlug size={16} /> {!collapsed && <span>MCP</span>}
              </div>
            )}
            <div className={`navItem ${view === "plugins" ? "navItemActive" : ""}`} onClick={() => onViewChange("plugins")} role="button" tabIndex={0} title={t("sidebar.plugins")}>
              <IconPuzzle size={16} /> {!collapsed && <span>{t("sidebar.plugins")} {BETA_SUP}</span>}
            </div>
            <div className={`navItem ${view === "memory" ? "navItemActive" : ""}`} onClick={() => onViewChange("memory")} role="button" tabIndex={0} title={t("sidebar.memory")}>
              <IconBrain size={16} /> {!collapsed && <span>{t("sidebar.memory")}</span>}
            </div>
            <div className={`navItem ${view === "scheduler" ? "navItemActive" : ""}`} onClick={() => onViewChange("scheduler")} role="button" tabIndex={0} title={t("sidebar.scheduler")}>
              <IconCalendar size={16} /> {!collapsed && <span>{t("sidebar.scheduler")}</span>}
            </div>
          </div>
        )}

        {/* ── Group: Apps (Plugin 2.0 UI plugins) ── */}
        {pluginApps.length > 0 && (
          <>
            <NavGroupHeader collapsed={collapsed} icon={<IconLayoutGrid size={GROUP_ICON_SIZE} />} label={t("sidebar.groupApps", "Apps")} expanded={appsExpanded} onToggle={() => toggleGroup("apps")} />
            {(collapsed || appsExpanded) && (
              <div className="navGroupItems">
                {pluginApps.map(app => {
                  const appViewId: ViewId = `plugin_app:${app.id}`;
                  const appTitle = pickAppTitle(app);
                  return (
                    <div
                      key={app.id}
                      className={`navItem ${view === appViewId ? "navItemActive" : ""}`}
                      onClick={() => onViewChange(appViewId)}
                      role="button"
                      tabIndex={0}
                      title={appTitle}
                    >
                      {app.icon_url ? (
                        <img src={`${httpApiBase}${app.icon_url}`} alt="" style={{ width: 16, height: 16, borderRadius: 2 }} />
                      ) : (
                        <IconLayoutGrid size={16} />
                      )}
                      {!collapsed && <span>{appTitle}</span>}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* ── Group: Monitor ── */}
        <NavGroupHeader collapsed={collapsed} icon={<IconRadar size={GROUP_ICON_SIZE} />} label={t("sidebar.groupMonitor")} expanded={monExpanded} onToggle={() => toggleGroup("monitor")} />
        {(collapsed || monExpanded) && (
          <div className="navGroupItems">
            <div className={`navItem ${view === "token_stats" ? "navItemActive" : ""}`} onClick={() => onViewChange("token_stats")} role="button" tabIndex={0} title={t("sidebar.tokenStats")} style={disabledViews.includes("token_stats") ? { opacity: 0.4 } : undefined}>
              <IconZap size={16} /> {!collapsed && <span>{t("sidebar.tokenStats")}</span>}
            </div>
            <div className={`navItem ${view === "skill_usage" ? "navItemActive" : ""}`} onClick={() => onViewChange("skill_usage")} role="button" tabIndex={0} title={t("sidebar.skillUsage")}>
              <IconBarChart size={16} /> {!collapsed && <span>{t("sidebar.skillUsage")}</span>}
            </div>
            <div className={`navItem ${view === "security" ? "navItemActive" : ""}`} onClick={() => onViewChange("security")} role="button" tabIndex={0} title={t("sidebar.security")}>
              <IconShield size={16} /> {!collapsed && <span>{t("sidebar.security")}</span>}
            </div>
            <div className={`navItem ${view === "pending_approvals" ? "navItemActive" : ""}`} onClick={() => onViewChange("pending_approvals")} role="button" tabIndex={0} title={t("sidebar.pendingApprovals")} style={{ position: "relative" }}>
              <IconFingerprint size={16} /> {!collapsed && <span>{t("sidebar.pendingApprovals")}</span>}
              {(pendingApprovalsCount ?? 0) > 0 && (
                <span style={{
                  position: "absolute", top: 4, left: collapsed ? 22 : undefined, right: collapsed ? undefined : 8,
                  minWidth: 16, height: 16, borderRadius: 8,
                  background: "#ef4444", color: "#fff", fontSize: 10, fontWeight: 600,
                  display: "flex", alignItems: "center", justifyContent: "center", padding: "0 4px",
                }}>{pendingApprovalsCount}</span>
              )}
            </div>
          </div>
        )}

        {/* ── Group: Multi-Agent ── */}
        <NavGroupHeader collapsed={collapsed} icon={<IconBot size={GROUP_ICON_SIZE} />} label={t("sidebar.groupMultiAgent")} expanded={maExpanded} onToggle={() => toggleGroup("multiAgent")} />
        {(collapsed || maExpanded) && (
          <div className="navGroupItems">
            <div className={`navItem ${view === "dashboard" ? "navItemActive" : ""}`} onClick={() => onViewChange("dashboard")} role="button" tabIndex={0} title={t("sidebar.dashboard")}>
              <IconUsers size={16} /> {!collapsed && <span>{t("sidebar.dashboard")} {BETA_SUP}</span>}
            </div>
            <div className={`navItem ${view === "org_editor" ? "navItemActive" : ""}`} onClick={() => onViewChange("org_editor")} role="button" tabIndex={0} title={t("sidebar.orgEditor")}>
              <IconLayoutGrid size={16} /> {!collapsed && <span>{t("sidebar.orgEditor")} {BETA_SUP}</span>}
            </div>
            <div className={`navItem ${view === "pixel_office" ? "navItemActive" : ""}`} onClick={() => onViewChange("pixel_office")} role="button" tabIndex={0} title={t("sidebar.pixelOffice")}>
              <IconBuilding size={16} /> {!collapsed && <span>{t("sidebar.pixelOffice")} {BETA_SUP}</span>}
            </div>
            <div className={`navItem ${view === "agent_manager" ? "navItemActive" : ""}`} onClick={() => onViewChange("agent_manager")} role="button" tabIndex={0} title={t("sidebar.agentManager")}>
              <IconBot size={16} /> {!collapsed && <span>{t("sidebar.agentManager")}</span>}
            </div>
          </div>
        )}

        {/* ── Group: Store ── */}
        {storeVisible && (
          <>
            <NavGroupHeader collapsed={collapsed} icon={<IconStorefront size={GROUP_ICON_SIZE} />} label={t("sidebar.groupStore")} expanded={stExpanded} onToggle={() => toggleGroup("store")} />
            {(collapsed || stExpanded) && (
              <div className="navGroupItems">
                <div className={`navItem ${view === "agent_store" ? "navItemActive" : ""}`} onClick={() => onViewChange("agent_store")} role="button" tabIndex={0} title={t("sidebar.agentStore")}>
                  <IconStorefront size={16} /> {!collapsed && <span>{t("sidebar.agentStore")} {BETA_SUP}</span>}
                </div>
                <div className={`navItem ${view === "skill_store" ? "navItemActive" : ""}`} onClick={() => onViewChange("skill_store")} role="button" tabIndex={0} title={t("sidebar.skillStore")}>
                  <IconPuzzle size={16} /> {!collapsed && <span>{t("sidebar.skillStore")} {BETA_SUP}</span>}
                </div>
              </div>
            )}
          </>
        )}
      </div>}

      <div className="sidebarAccountArea" ref={accountAreaRef}>
        {accountMenuOpen && (
          <div
            className="sidebarAccountMenu"
            role="menu"
            aria-label={t(accountEnabled ? "sidebar.accountMenu" : "sidebar.appMenu")}
          >
            {accountEnabled && (accountSignedIn ? (
              <div className="sidebarAccountMenuProfile">
                {accountUsesOpenAkitaBrand ? (
                  <img src={logoUrl} alt="" className="sidebarAccountMenuAvatar" />
                ) : (
                  <span className="sidebarAccountMenuAvatar sidebarAccountAvatarPlaceholder" aria-hidden="true">
                    <IconUsers size={18} />
                  </span>
                )}
                <span className="sidebarAccountMenuIdentity">
                  <strong>{accountName}</strong>
                  <small>{accountDetail}</small>
                  {accountNeedsSync && (
                    <span className="sidebarAccountStatus">
                      <IconAlertCircle size={11} aria-hidden="true" />
                      {t("account.syncRequired")}
                    </span>
                  )}
                </span>
              </div>
            ) : (
              <button
                type="button"
                className="sidebarAccountMenuProfile sidebarAccountMenuProfileAction"
                onClick={() => { void startAccountLogin(); }}
                disabled={accountLoginPending}
                aria-busy={accountLoginPending}
                role="menuitem"
              >
                {accountLoginPending ? (
                  <span className="sidebarAccountMenuAvatar sidebarAccountAvatarPlaceholder" aria-hidden="true">
                    <IconRefresh size={18} className="spinIcon" />
                  </span>
                ) : (
                  <span className="sidebarAccountMenuAvatar sidebarAccountAvatarPlaceholder" aria-hidden="true">
                    <IconHelp size={18} />
                  </span>
                )}
                <span className="sidebarAccountMenuIdentity">
                  <strong>{accountName}</strong>
                  <small>{accountDetail}</small>
                </span>
              </button>
            ))}
            {accountEnabled && !accountSignedIn && accountLoginError && (
              <div className="sidebarAccountMenuError" role="alert">
                <IconAlertCircle size={15} aria-hidden="true" />
                <span>{accountLoginError}</span>
              </div>
            )}
            {accountEnabled && accountNeedsSync && accountSnapshot?.status_reason && (
              <div className="sidebarAccountMenuNotice" role="status">
                <IconAlertCircle size={15} aria-hidden="true" />
                <span>{accountSnapshot.status_reason}</span>
              </div>
            )}
            {accountEnabled && <div className="sidebarAccountMenuDivider" />}
            {accountNeedsSync && (
              <button
                type="button"
                className="sidebarAccountMenuItem"
                onClick={() => { void refreshAccountEntitlements(); }}
                disabled={accountActionPending !== null}
                role="menuitem"
              >
                <IconRefresh size={17} className={accountActionPending === "refresh" ? "spinIcon" : undefined} />
                <span>{t("account.syncEntitlements")}</span>
              </button>
            )}
            <button
              type="button"
              className="sidebarAccountMenuItem"
              onClick={() => selectAccountMenuItem(onEnterConfig)}
              role="menuitem"
            >
              <IconConfig size={17} />
              <span>{t("sidebar.config")}</span>
              <IconChevronRight size={14} className="sidebarAccountMenuChevron" />
            </button>
            <button
              type="button"
              className="sidebarAccountMenuItem"
              onClick={() => selectAccountMenuItem(() => onViewChange("my_feedback"))}
              disabled={!serviceRunning}
              role="menuitem"
            >
              <span className="sidebarAccountMenuIconWrap">
                <IconBug size={17} />
                {(unreadFeedbackCount ?? 0) > 0 && <span className="sidebarMenuUnreadDot" />}
              </span>
              <span>{t("sidebar.myFeedback")}</span>
            </button>
            {onCheckForUpdate && (
              <button
                type="button"
                className="sidebarAccountMenuItem"
                onClick={() => selectAccountMenuItem(() => { void onCheckForUpdate(); })}
                disabled={updateCheckPending}
                role="menuitem"
              >
                <IconRefresh size={17} className={updateCheckPending ? "spinIcon" : undefined} />
                <span>{updateCheckPending ? t("version.checking") : t("version.checkNow")}</span>
              </button>
            )}
            {accountSignedIn && (
              <>
                <div className="sidebarAccountMenuDivider" />
                <button
                  type="button"
                  className="sidebarAccountMenuItem sidebarAccountMenuDanger"
                  onClick={() => { void logoutAccount(); }}
                  disabled={accountActionPending !== null}
                  role="menuitem"
                >
                  {accountActionPending === "logout"
                    ? <IconRefresh size={17} className="spinIcon" />
                    : <LogOut size={17} aria-hidden="true" />}
                  <span>{accountActionPending === "logout" ? t("account.signingOut") : t("account.signOut")}</span>
                </button>
              </>
            )}
          </div>
        )}
        <button
          type="button"
          className={`sidebarAccountButton ${accountMenuOpen ? "sidebarAccountButtonActive" : ""}`}
          onClick={() => {
            if (collapsed) onToggleCollapsed();
            setAccountMenuOpen((open) => !open);
          }}
          aria-haspopup="menu"
          aria-expanded={accountMenuOpen}
          aria-busy={accountEnabled && accountLoginPending}
          title={accountEnabled ? accountName : t("sidebar.appMenu")}
        >
          {!accountEnabled ? (
            <span className="sidebarAccountAvatar sidebarAccountAvatarPlaceholder" aria-hidden="true">
              <IconConfig size={18} />
            </span>
          ) : accountLoginPending ? (
            <span className="sidebarAccountAvatar sidebarAccountAvatarPlaceholder" aria-hidden="true">
              <IconRefresh size={18} className="spinIcon" />
            </span>
          ) : accountSignedIn && accountUsesOpenAkitaBrand ? (
            <img src={logoUrl} alt="" className="sidebarAccountAvatar" />
          ) : accountSignedIn ? (
            <span className="sidebarAccountAvatar sidebarAccountAvatarPlaceholder" aria-hidden="true">
              <IconUsers size={18} />
            </span>
          ) : (
            <span className="sidebarAccountAvatar sidebarAccountAvatarPlaceholder" aria-hidden="true">
              <IconHelp size={18} />
            </span>
          )}
          {!collapsed && (
            <>
              <span className="sidebarAccountIdentity">
                <strong>{accountEnabled ? accountName : t("sidebar.appMenu")}</strong>
                <small>{accountEnabled ? accountDetail : t("sidebar.appMenuHint")}</small>
              </span>
              <span className="sidebarAccountToggle">
                <IconChevronDown size={14} />
              </span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
