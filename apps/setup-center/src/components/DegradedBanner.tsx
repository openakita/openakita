/**
 * DegradedBanner + DegradedRepairDialog
 *
 * Surfaces the v1.29 boot-fault-tolerance "degraded subsystem" state to
 * the user. When the backend reports one or more degraded subsystems in
 * its ``/api/health`` response, this component:
 *
 *  1. Renders a yellow banner at the top of the app with a count.
 *  2. Opens a dialog (on click) listing each subsystem with a per-row
 *     "quarantine + recreate" action.
 *  3. Differentiates the "restart required" UX between Tauri (auto-
 *     restart via ``/api/shutdown``) and standalone ``openakita serve``
 *     (manual restart instructions).
 *
 * Polling strategy is deliberately conservative — initial fetch on
 * mount, refetch on a custom event when the user manually clicks the
 * banner, and a passive listener to a custom event so other parts of
 * the app can request a refresh (e.g. after WS reconnect). No fixed
 * interval polling to avoid generating background noise.
 */
import React from "react";
import { useTranslation } from "react-i18next";

import { authFetch } from "../platform/auth";
import { IS_TAURI } from "../platform/detect";
import { invoke, onWsEvent } from "../platform";

const HEALTH_URL = "/api/health";
const DEGRADED_GET_URL = "/api/memory/repair/degraded";
const QUARANTINE_URL = "/api/memory/repair/quarantine";
const SHUTDOWN_URL = "/api/shutdown";

export const DEGRADED_REFRESH_EVENT = "openakita.degraded.refresh";

/**
 * Pull the per-process desktop session token out of Tauri. In web/standalone
 * mode this header is not required (the backend skips the check when the
 * env var isn't set), so we return `null` and let the request go through.
 *
 * Mirrors StatusView's memory-repair flow to avoid drift between the two
 * sites that touch destructive repair endpoints.
 */
async function getDesktopToken(): Promise<string | null> {
  if (!IS_TAURI) return null;
  try {
    return await invoke<string>("openakita_desktop_session_token");
  } catch {
    return null;
  }
}

export interface DegradedEntry {
  subsystem: string;
  reason: string;
  since?: string;
  details?: string;
  repair_action?: string;
}

interface HealthResponse {
  degraded_subsystems?: DegradedEntry[];
}

interface DegradedGetResponse {
  subsystems: DegradedEntry[];
  desktop_token_required: boolean;
  confirmation_token: string;
}

async function fetchHealthSnapshot(apiBase: string): Promise<DegradedEntry[]> {
  try {
    const res = await authFetch(`${apiBase}${HEALTH_URL}`, { method: "GET" }, apiBase);
    if (!res.ok) return [];
    const data = (await res.json()) as HealthResponse;
    return Array.isArray(data?.degraded_subsystems) ? data.degraded_subsystems : [];
  } catch {
    return [];
  }
}

async function fetchDegradedDetails(apiBase: string): Promise<DegradedGetResponse | null> {
  try {
    const res = await authFetch(`${apiBase}${DEGRADED_GET_URL}`, { method: "GET" }, apiBase);
    if (!res.ok) return null;
    return (await res.json()) as DegradedGetResponse;
  } catch {
    return null;
  }
}

interface Props {
  apiBase: string;
  /**
   * Optional override — when running standalone ``openakita serve`` from
   * a browser tab the banner can't trigger ``/api/shutdown`` (the user
   * would lose the only way back). We default to ``IS_TAURI``, but
   * callers can force the standalone path.
   */
  canAutoRestart?: boolean;
}

export const DegradedBanner: React.FC<Props> = ({
  apiBase,
  canAutoRestart = IS_TAURI,
}) => {
  const { t } = useTranslation();
  const [entries, setEntries] = React.useState<DegradedEntry[]>([]);
  const [dialogOpen, setDialogOpen] = React.useState(false);

  const refresh = React.useCallback(async () => {
    const next = await fetchHealthSnapshot(apiBase);
    setEntries(next);
  }, [apiBase]);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = await fetchHealthSnapshot(apiBase);
      if (!cancelled) setEntries(next);
    })();
    const handler = () => {
      void refresh();
    };
    window.addEventListener(DEGRADED_REFRESH_EVENT, handler);

    // 1) Tab regained visibility → refresh (catches both Tauri window focus
    //    and browser tab switches; cheap, runs at most a couple of times).
    const onVis = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVis);

    // 2) WS reconnect → refresh. We don't have a dedicated reconnect event,
    //    so we piggyback on the first message after a quiet period. Any
    //    message proves the socket is alive; ``ws_reconnected`` is fired by
    //    the platform layer on successful reconnect (best-effort; falls back
    //    to debounce on any traffic).
    let lastSeen = 0;
    const RECONNECT_GAP_MS = 30_000;
    const unsubscribeWs = onWsEvent((event: string) => {
      const now = Date.now();
      if (event === "ws_reconnected" || (lastSeen > 0 && now - lastSeen > RECONNECT_GAP_MS)) {
        void refresh();
      }
      lastSeen = now;
    });

    return () => {
      cancelled = true;
      window.removeEventListener(DEGRADED_REFRESH_EVENT, handler);
      document.removeEventListener("visibilitychange", onVis);
      try {
        unsubscribeWs();
      } catch {
        // ignore
      }
    };
  }, [apiBase, refresh]);

  if (entries.length === 0) return null;

  return (
    <>
      <div
        role="alert"
        className="degradedBanner"
        onClick={() => setDialogOpen(true)}
        style={{
          background: "linear-gradient(135deg, #facc15, #f59e0b)",
          color: "#1f2937",
          padding: "8px 16px",
          fontSize: 13,
          fontWeight: 600,
          cursor: "pointer",
          textAlign: "center",
          borderBottom: "1px solid #c2410c",
        }}
      >
        {t("degraded.bannerSummary", {
          count: entries.length,
          defaultValue: "{{count}} subsystem(s) degraded — click to repair",
        })}
      </div>
      {dialogOpen && (
        <DegradedRepairDialog
          apiBase={apiBase}
          canAutoRestart={canAutoRestart}
          onClose={() => {
            setDialogOpen(false);
            void refresh();
          }}
        />
      )}
    </>
  );
};

interface DialogProps {
  apiBase: string;
  canAutoRestart: boolean;
  onClose: () => void;
}

interface RowStatus {
  loading: boolean;
  done: boolean;
  error?: string;
}

const DegradedRepairDialog: React.FC<DialogProps> = ({ apiBase, canAutoRestart, onClose }) => {
  const { t } = useTranslation();
  const [details, setDetails] = React.useState<DegradedGetResponse | null>(null);
  const [rowStatus, setRowStatus] = React.useState<Record<string, RowStatus>>({});
  const [restartReady, setRestartReady] = React.useState(false);

  const reload = React.useCallback(async () => {
    const next = await fetchDegradedDetails(apiBase);
    setDetails(next);
  }, [apiBase]);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  const quarantine = async (subsystem: string) => {
    setRowStatus((s) => ({ ...s, [subsystem]: { loading: true, done: false } }));
    // Fetch a fresh confirmation token for each operation. The token
    // issued by the initial GET expires after 5 minutes, and we don't
    // want the dialog UX to suddenly fail if the user spends time
    // deliberating.
    const fresh = await fetchDegradedDetails(apiBase);
    if (!fresh) {
      setRowStatus((s) => ({
        ...s,
        [subsystem]: { loading: false, done: false, error: t("degraded.errorNoToken", { defaultValue: "Failed to acquire confirmation token" }) },
      }));
      return;
    }
    try {
      // The backend's ``/api/memory/repair/quarantine`` requires the desktop
      // session token whenever ``OPENAKITA_DESKTOP_SESSION_TOKEN`` is set in
      // the backend env (which Tauri always does for its bundled backend).
      // Skipping this header in Tauri builds caused production users to get
      // a silent 403 here even though Web/standalone mode worked fine.
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      const desktopToken = await getDesktopToken();
      if (desktopToken) {
        headers["X-OpenAkita-Desktop-Token"] = desktopToken;
      }
      const res = await authFetch(
        `${apiBase}${QUARANTINE_URL}`,
        {
          method: "POST",
          headers,
          body: JSON.stringify({
            subsystem,
            confirmation_token: fresh.confirmation_token,
          }),
        },
        apiBase,
      );
      if (!res.ok) {
        const errBody = await res.text();
        throw new Error(`HTTP ${res.status}: ${errBody.slice(0, 200)}`);
      }
      setRowStatus((s) => ({ ...s, [subsystem]: { loading: false, done: true } }));
      setRestartReady(true);
      // Refresh details so this row drops off the list.
      void reload();
      window.dispatchEvent(new Event(DEGRADED_REFRESH_EVENT));
    } catch (e) {
      setRowStatus((s) => ({
        ...s,
        [subsystem]: { loading: false, done: false, error: String((e as Error)?.message || e) },
      }));
    }
  };

  const restart = async () => {
    if (!canAutoRestart) return;
    try {
      await authFetch(`${apiBase}${SHUTDOWN_URL}`, { method: "POST" }, apiBase);
    } catch {
      // ignore — when Tauri kills the process the request will fail
    }
    // Give the desktop shell a chance to respawn the backend, then reload.
    window.setTimeout(() => {
      window.location.reload();
    }, 6_000);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="degradedRepairOverlay"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 10_000,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#fff",
          color: "#111827",
          width: "min(560px, 92vw)",
          maxHeight: "90vh",
          overflow: "auto",
          borderRadius: 12,
          padding: 20,
          boxShadow: "0 20px 50px rgba(0,0,0,0.25)",
        }}
      >
        <h2 style={{ marginTop: 0, fontSize: 18, fontWeight: 700 }}>
          {t("degraded.dialogTitle", { defaultValue: "Degraded subsystems" })}
        </h2>
        <p style={{ fontSize: 13, color: "#374151", margin: "8px 0 16px" }}>
          {t("degraded.dialogDescription", {
            defaultValue:
              "OpenAkita detected one or more subsystems unable to open their database. You can quarantine each one (rename the file, recreate empty on next boot) — no other data is touched.",
          })}
        </p>

        {details === null && (
          <p>{t("degraded.loading", { defaultValue: "Loading…" })}</p>
        )}

        {details !== null && details.subsystems.length === 0 && (
          <p>{t("degraded.allHealthy", { defaultValue: "All subsystems healthy." })}</p>
        )}

        {details !== null && details.subsystems.map((entry) => {
          const status = rowStatus[entry.subsystem] || { loading: false, done: false };
          // ``memory`` has a richer repair flow (restore from backup /
          // snapshot) under the Status view, which the generic
          // quarantine endpoint deliberately does NOT support. Route
          // the user there instead of offering a destructive button
          // that the backend would reject with 400.
          const isMemory = entry.subsystem === "memory";
          // ``token_tracking`` quarantines ``agent.db`` — the same file
          // that the legacy ``storage.Database`` and token-stats API
          // read from. Surface that so the user isn't surprised when
          // their token usage history vanishes after the click.
          const wideImpact = entry.subsystem === "token_tracking";
          return (
            <div
              key={entry.subsystem}
              style={{
                border: "1px solid #e5e7eb",
                borderRadius: 8,
                padding: 12,
                marginBottom: 8,
              }}
            >
              <div style={{ fontWeight: 600 }}>{entry.subsystem}</div>
              <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>
                {t("degraded.reasonLabel", { defaultValue: "reason" })}: {entry.reason}
              </div>
              {entry.since && (
                <div style={{ fontSize: 12, color: "#6b7280" }}>
                  {t("degraded.sinceLabel", { defaultValue: "since" })}: {entry.since}
                </div>
              )}
              {entry.details && (
                <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 4 }}>
                  {entry.details}
                </div>
              )}
              {wideImpact && (
                <div
                  style={{
                    fontSize: 11,
                    color: "#b45309",
                    marginTop: 6,
                    background: "#fef3c7",
                    border: "1px solid #fcd34d",
                    borderRadius: 4,
                    padding: "4px 6px",
                  }}
                >
                  {t("degraded.wideImpactWarning", {
                    defaultValue:
                      "Quarantining this resets agent.db — token usage history and legacy stats will be cleared.",
                  })}
                </div>
              )}
              <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
                {isMemory ? (
                  <button
                    onClick={() => {
                      window.location.hash = "#/status";
                      onClose();
                    }}
                    style={{
                      background: "#2563eb",
                      color: "#fff",
                      border: "none",
                      borderRadius: 6,
                      padding: "6px 12px",
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    {t("degraded.openMemoryRepair", {
                      defaultValue: "Open memory repair tool",
                    })}
                  </button>
                ) : (
                  <button
                    disabled={status.loading || status.done}
                    onClick={() => quarantine(entry.subsystem)}
                    style={{
                      background: status.done ? "#10b981" : "#ef4444",
                      color: "#fff",
                      border: "none",
                      borderRadius: 6,
                      padding: "6px 12px",
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: status.loading || status.done ? "default" : "pointer",
                    }}
                  >
                    {status.done
                      ? t("degraded.quarantined", { defaultValue: "Quarantined" })
                      : status.loading
                        ? t("degraded.quarantining", { defaultValue: "Quarantining…" })
                        : t("degraded.quarantineBtn", { defaultValue: "Quarantine + recreate" })}
                  </button>
                )}
                {status.error && (
                  <span style={{ fontSize: 11, color: "#dc2626" }}>{status.error}</span>
                )}
              </div>
            </div>
          );
        })}

        {restartReady && (
          <div
            style={{
              marginTop: 16,
              padding: 12,
              borderRadius: 8,
              background: "#fef3c7",
              border: "1px solid #f59e0b",
              fontSize: 13,
            }}
          >
            <strong>
              {t("degraded.restartTitle", { defaultValue: "Restart required" })}
            </strong>
            <div style={{ marginTop: 6 }}>
              {canAutoRestart
                ? t("degraded.restartTauri", {
                    defaultValue:
                      "OpenAkita will stop the backend; the desktop app will respawn it automatically.",
                  })
                : t("degraded.restartStandalone", {
                    defaultValue:
                      "Stop the current backend with Ctrl+C in your terminal and run `openakita serve` again.",
                  })}
            </div>
            {canAutoRestart && (
              <button
                onClick={restart}
                style={{
                  marginTop: 8,
                  background: "#1f2937",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  padding: "6px 14px",
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                {t("degraded.restartNow", { defaultValue: "Restart now" })}
              </button>
            )}
          </div>
        )}

        <div style={{ marginTop: 16, textAlign: "right" }}>
          <button
            onClick={onClose}
            style={{
              background: "#e5e7eb",
              color: "#1f2937",
              border: "none",
              borderRadius: 6,
              padding: "6px 14px",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {t("common.close", { defaultValue: "Close" })}
          </button>
        </div>
      </div>
    </div>
  );
};

export default DegradedBanner;
