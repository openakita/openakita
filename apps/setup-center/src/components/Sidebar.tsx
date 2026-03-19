import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import type { StepId, Step } from "../types";
import {
  IconChat, IconIM, IconSkills, IconStatus, IconConfig,
  IconChevronDown, IconChevronRight, IconGlobe,
  IconZap, IconPlug, IconCalendar,
  IconBug, IconBrain, IconGitHub, IconGitee, IconUsers, IconBot,
  IconGear, IconBook, IconStorefront, IconPuzzle, IconFingerprint, IconLayoutGrid,
} from "../icons";
import logoUrl from "../assets/logo.png";

type ViewId = "wizard" | "status" | "chat" | "skills" | "im" | "onboarding" | "modules" | "token_stats" | "mcp" | "scheduler" | "memory" | "identity" | "dashboard" | "org_editor" | "agent_manager" | "agent_store" | "skill_store" | "docs";

export type SidebarProps = {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  view: ViewId;
  onViewChange: (v: ViewId) => void;
  configExpanded: boolean;
  onToggleConfig: () => void;
  steps: Step[];
  stepId: StepId;
  onStepChange: (id: StepId) => void;
  disabledViews: string[];
  multiAgentEnabled: boolean;
  onToggleMultiAgent: () => void;
  storeVisible: boolean;
  desktopVersion: string;
  backendVersion: string | null;
  serviceRunning: boolean;
  onBugReport: () => void;
  onRefreshStatus: () => Promise<void>;
  isWeb?: boolean;
  mobileOpen?: boolean;
};

const stepIcons: Partial<Record<StepId, React.ReactNode>> = {
  llm: <IconZap size={14} />,
  im: <IconIM size={14} />,
  tools: <IconSkills size={14} />,
  agent: <IconBot size={14} />,
  workspace: <IconBook size={14} />,
  advanced: <IconGear size={14} />,
};

function StepDot({ stepId: sid }: { stepId: StepId }) {
  return <div className="stepDot">{stepIcons[sid]}</div>;
}

export function Sidebar({
  collapsed, onToggleCollapsed,
  view, onViewChange,
  configExpanded, onToggleConfig,
  steps, stepId, onStepChange,
  disabledViews, multiAgentEnabled, onToggleMultiAgent,
  storeVisible,
  desktopVersion, backendVersion, serviceRunning,
  onBugReport, onRefreshStatus, isWeb, mobileOpen,
}: SidebarProps) {
  const { t } = useTranslation();

  return (
    <aside className={`sidebar ${collapsed ? "sidebarCollapsed" : ""}${mobileOpen ? " sidebarOpen" : ""}`}>
      <div className="sidebarHeader">
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
      </div>

      {/* Primary nav */}
      <div className="sidebarNav">
        <div className={`navItem ${view === "chat" ? "navItemActive" : ""}`} onClick={() => onViewChange("chat")} role="button" tabIndex={0} title={t("sidebar.chat")}>
          <IconChat size={16} /> {!collapsed && <span>{t("sidebar.chat")}</span>}
        </div>
        {!disabledViews.includes("im") && (
          <div className={`navItem ${view === "im" ? "navItemActive" : ""}`} onClick={() => onViewChange("im")} role="button" tabIndex={0} title={t("sidebar.im")}>
            <IconIM size={16} /> {!collapsed && <span>{t("sidebar.im")}</span>}
          </div>
        )}
        {!disabledViews.includes("skills") && (
          <div className={`navItem ${view === "skills" ? "navItemActive" : ""}`} onClick={() => onViewChange("skills")} role="button" tabIndex={0} title={t("sidebar.skills")}>
            <IconSkills size={16} /> {!collapsed && <span>{t("sidebar.skills")}</span>}
          </div>
        )}
        {!disabledViews.includes("mcp") && (
          <div className={`navItem ${view === "mcp" ? "navItemActive" : ""}`} onClick={() => onViewChange("mcp")} role="button" tabIndex={0} title="MCP">
            <IconPlug size={16} /> {!collapsed && <span>MCP <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup></span>}
          </div>
        )}
        <div className={`navItem ${view === "scheduler" ? "navItemActive" : ""}`} onClick={() => onViewChange("scheduler")} role="button" tabIndex={0} title={t("sidebar.scheduler")} style={disabledViews.includes("scheduler") ? { opacity: 0.4 } : undefined}>
          <IconCalendar size={16} /> {!collapsed && <span>{t("sidebar.scheduler")} <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup></span>}
        </div>
        <div className={`navItem ${view === "memory" ? "navItemActive" : ""}`} onClick={() => onViewChange("memory")} role="button" tabIndex={0} title={t("sidebar.memory")} style={disabledViews.includes("memory") ? { opacity: 0.4 } : undefined}>
          <IconBrain size={16} /> {!collapsed && <span>{t("sidebar.memory")} <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup></span>}
        </div>
        <div className={`navItem ${view === "status" ? "navItemActive" : ""}`} onClick={async () => { onViewChange("status"); try { await onRefreshStatus(); } catch { /* ignore */ } }} role="button" tabIndex={0} title={t("sidebar.status")}>
          <IconStatus size={16} /> {!collapsed && <span>{t("sidebar.status")}</span>}
        </div>
        <div className={`navItem ${view === "token_stats" ? "navItemActive" : ""}`} onClick={() => onViewChange("token_stats")} role="button" tabIndex={0} title={t("sidebar.tokenStats")} style={disabledViews.includes("token_stats") ? { opacity: 0.4 } : undefined}>
          <IconZap size={16} /> {!collapsed && <span>{t("sidebar.tokenStats")}</span>}
        </div>
        {multiAgentEnabled && (
          <div className={`navItem ${view === "dashboard" ? "navItemActive" : ""}`} onClick={() => onViewChange("dashboard")} role="button" tabIndex={0} title={t("sidebar.dashboard")}>
            <IconUsers size={16} /> {!collapsed && <span>{t("sidebar.dashboard")} <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup></span>}
          </div>
        )}
        {multiAgentEnabled && (
          <div className={`navItem ${view === "org_editor" ? "navItemActive" : ""}`} onClick={() => onViewChange("org_editor")} role="button" tabIndex={0} title={t("sidebar.orgEditor")}>
            <IconLayoutGrid size={16} /> {!collapsed && <span>{t("sidebar.orgEditor")} <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup></span>}
          </div>
        )}
        {multiAgentEnabled && (
          <div className={`navItem ${view === "agent_manager" ? "navItemActive" : ""}`} onClick={() => onViewChange("agent_manager")} role="button" tabIndex={0} title={t("sidebar.agentManager")}>
            <IconBot size={16} /> {!collapsed && <span>{t("sidebar.agentManager")}</span>}
          </div>
        )}
        {storeVisible && (
          <>
            <div style={{ height: 1, background: "var(--line)", margin: "6px 12px" }} />
            <div className={`navItem ${view === "agent_store" ? "navItemActive" : ""}`} onClick={() => onViewChange("agent_store")} role="button" tabIndex={0} title={t("sidebar.agentStore")}>
              <IconStorefront size={16} /> {!collapsed && <span>{t("sidebar.agentStore")} <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup></span>}
            </div>
            <div className={`navItem ${view === "skill_store" ? "navItemActive" : ""}`} onClick={() => onViewChange("skill_store")} role="button" tabIndex={0} title={t("sidebar.skillStore")}>
              <IconPuzzle size={16} /> {!collapsed && <span>{t("sidebar.skillStore")} <sup style={{ fontSize: 9, color: "var(--primary, #3b82f6)", fontWeight: 600 }}>Beta</sup></span>}
            </div>
          </>
        )}
      </div>

      {/* Collapsible Config section */}
      <div className="configSection">
        <div className="configHeader" onClick={onToggleConfig} role="button" tabIndex={0} title={t("sidebar.config")}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <IconConfig size={16} />
            {!collapsed && <span>{t("sidebar.config")}</span>}
          </div>
          {!collapsed && (
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {configExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            </div>
          )}
        </div>
        {!collapsed && configExpanded && (
          <div className="stepList">
            {steps.map((s) => {
              const isActive = view === "wizard" && s.id === stepId;
              return (
                <Fragment key={s.id}>
                  <div
                    className={`stepItem ${isActive ? "stepItemActive" : ""}`}
                    onClick={() => { onViewChange("wizard"); onStepChange(s.id); }}
                    role="button" tabIndex={0}
                  >
                    <StepDot stepId={s.id} />
                    <div className="stepMeta"><div className="stepTitle">{s.title}</div></div>
                  </div>
                  {s.id === "agent" && (
                    <div
                      className={`stepItem ${view === "identity" ? "stepItemActive" : ""}`}
                      onClick={() => onViewChange("identity")}
                      role="button" tabIndex={0}
                      title={t("sidebar.identity")}
                    >
                      <div className="stepDot"><IconFingerprint size={14} /></div>
                      <div className="stepMeta"><div className="stepTitle">{t("sidebar.identity")}</div></div>
                    </div>
                  )}
                </Fragment>
              );
            })}
          </div>
        )}
      </div>

      {/* Multi-Agent Mode Toggle */}
      {!collapsed && (
        <div style={{
          padding: "12px 18px",
          borderTop: "1px solid var(--line)",
          flexShrink: 0,
        }}>
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: 13, color: "var(--fg)" }}>
                {t("config.multiAgentMode")}
              </span>
              <span style={{
                fontSize: 10, padding: "1px 5px", borderRadius: 4,
                background: "var(--accent)", color: "#fff",
                fontWeight: 600, letterSpacing: 0.5,
              }}>
                {t("config.multiAgentBeta")}
              </span>
            </div>
            <div
              onClick={onToggleMultiAgent}
              style={{
                width: 40, height: 22, borderRadius: 11, cursor: "pointer",
                background: multiAgentEnabled ? "var(--ok)" : "var(--line)",
                position: "relative", transition: "background 0.2s",
              }}
            >
              <div style={{
                width: 18, height: 18, borderRadius: 9, background: "#fff",
                position: "absolute", top: 2,
                left: multiAgentEnabled ? 20 : 2,
                transition: "left 0.2s", boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
              }} />
            </div>
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
            {multiAgentEnabled ? t("config.multiAgentOn") : t("config.multiAgentOff")}
          </div>
        </div>
      )}

      {/* Version info + website link + bug report at sidebar bottom */}
      {!collapsed && (
        <div style={{
          padding: "10px 16px",
          borderTop: "1px solid var(--line)",
          fontSize: 11,
          opacity: 0.4,
          lineHeight: 1.6,
          flexShrink: 0,
        }}>
          <div>{isWeb ? "Web" : "Desktop"} v{desktopVersion}{import.meta.env.VITE_PREVIEW_BUILD === "true" && <span style={{ marginLeft: 6, color: "#e8a735", fontWeight: 600, opacity: 1 }}>预览版</span>}</div>
          {backendVersion && <div>Backend v{backendVersion}</div>}
          {!backendVersion && serviceRunning && <div>Backend: -</div>}
          <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <a
              href="https://openakita.ai"
              style={{ color: "var(--accent, #5B8DEF)", textDecoration: "none", opacity: 1, display: "inline-flex", alignItems: "center", gap: 3 }}
              onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
              onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
            >
              <IconGlobe size={11} />
              openakita.ai
            </a>
            {serviceRunning && (
              <span
                onClick={onBugReport}
                title={t("feedback.trigger")}
                style={{ cursor: "pointer", opacity: 1, color: "var(--accent, #5B8DEF)", display: "inline-flex", alignItems: "center", gap: 2 }}
                onMouseEnter={(e) => { const s = e.currentTarget.querySelector<HTMLElement>(".feedbackText"); if (s) s.style.textDecoration = "underline"; }}
                onMouseLeave={(e) => { const s = e.currentTarget.querySelector<HTMLElement>(".feedbackText"); if (s) s.style.textDecoration = "none"; }}
              >
                <IconBug size={12} />
                <span className="feedbackText" style={{ fontSize: 11 }}>{t("feedback.trigger")}</span>
              </span>
            )}
            <span
              onClick={() => onViewChange("docs")}
              style={{ color: "var(--accent, #5B8DEF)", textDecoration: "none", opacity: 1, display: "inline-flex", alignItems: "center", gap: 3, cursor: "pointer" }}
              onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
              onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
              title={t("sidebar.docs")}
            >
              <IconBook size={12} />
              {t("sidebar.docs")}
            </span>
            <a
              href="https://github.com/openakita/openakita"
              title="GitHub"
              style={{ color: "var(--accent, #5B8DEF)", opacity: 1, display: "inline-flex", alignItems: "center" }}
            >
              <IconGitHub size={13} />
            </a>
            <a
              href="https://gitee.com/zacon365/openakita"
              title="Gitee"
              style={{ color: "var(--accent, #5B8DEF)", opacity: 1, display: "inline-flex", alignItems: "center" }}
            >
              <IconGitee size={13} />
            </a>
          </div>
        </div>
      )}
      {collapsed && (
        <div style={{
          padding: "8px 0",
          borderTop: "1px solid var(--line)",
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 6,
        }}>
          <div style={{ display: "flex", justifyContent: "center", gap: 8 }}>
            <a
              href="https://openakita.ai"
              title="openakita.ai"
              style={{ color: "var(--accent, #5B8DEF)", opacity: 0.5, display: "flex" }}
            >
              <IconGlobe size={14} />
            </a>
            {serviceRunning && (
              <span
                onClick={onBugReport}
                title={t("feedback.trigger")}
                style={{ color: "var(--accent, #5B8DEF)", opacity: 0.5, display: "flex", cursor: "pointer" }}
              >
                <IconBug size={14} />
              </span>
            )}
          </div>
          <div style={{ display: "flex", justifyContent: "center", gap: 8 }}>
            <span
              onClick={() => onViewChange("docs")}
              title={t("sidebar.docs")}
              style={{ color: "var(--accent, #5B8DEF)", opacity: 0.5, display: "flex", cursor: "pointer" }}
            >
              <IconBook size={14} />
            </span>
            <a
              href="https://github.com/openakita/openakita"
              title="GitHub"
              style={{ color: "var(--accent, #5B8DEF)", opacity: 0.5, display: "flex" }}
            >
              <IconGitHub size={14} />
            </a>
            <a
              href="https://gitee.com/zacon365/openakita"
              title="Gitee"
              style={{ color: "var(--accent, #5B8DEF)", opacity: 0.5, display: "flex" }}
            >
              <IconGitee size={14} />
            </a>
          </div>
        </div>
      )}
    </aside>
  );
}
