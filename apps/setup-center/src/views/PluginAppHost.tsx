/**
 * PluginAppHost -- renders a plugin's UI inside an iframe with
 * Bridge postMessage communication.
 *
 * Handles: loading skeleton, bridge init, theme/locale forwarding,
 * timeout soft-warning, hard error on iframe load failure, and full
 * cleanup / state reset on plugin switch.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { PluginBridgeHost } from "../lib/plugin-bridge-host";
import { getThemePref, THEME_CHANGE_EVENT } from "../theme";
import type { PluginUIApp, ViewId } from "../types";

export type PluginAppHostProps = {
  pluginId: string;
  apiBase: string;
  onViewChange?: (v: ViewId) => void;
};

/** Soft-warning timeout: after this we hint "loading is slow" but keep waiting. */
const BRIDGE_SLOW_MS = 8_000;

export default function PluginAppHost({ pluginId, apiBase, onViewChange }: PluginAppHostProps) {
  const { t, i18n } = useTranslation();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<PluginBridgeHost | null>(null);
  const connectedRef = useRef(false);
  const iframeLoadedRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [slow, setSlow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ title?: string; iconUrl?: string }>({});

  const handleNotification = useCallback((opts: { title: string; body: string; type?: string }) => {
    if (opts.type === "error") toast.error(opts.body);
    else if (opts.type === "warning") toast.warning(opts.body);
    else toast.success(opts.body);
  }, []);

  const handleNavigate = useCallback((viewId: string) => {
    if (onViewChange) onViewChange(viewId as ViewId);
  }, [onViewChange]);

  // Fetch this plugin's display metadata (title/icon) once per pluginId.
  // Decoupled from the bridge effect so a slow /ui-apps does not block UI.
  useEffect(() => {
    if (!apiBase || !pluginId) { setMeta({}); return; }
    let cancelled = false;
    fetch(`${apiBase}/api/plugins/ui-apps`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data: PluginUIApp[]) => {
        if (cancelled) return;
        const found = Array.isArray(data) ? data.find((a) => a.id === pluginId) : null;
        if (found) {
          setMeta({
            title: found.title,
            iconUrl: found.icon_url ? `${apiBase}${found.icon_url}` : undefined,
          });
        } else {
          setMeta({});
        }
      })
      .catch(() => { if (!cancelled) setMeta({}); });
    return () => { cancelled = true; };
  }, [pluginId, apiBase]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;

    // Reset all per-plugin state on switch (component is reused without remount).
    connectedRef.current = false;
    iframeLoadedRef.current = false;
    setLoading(true);
    setSlow(false);
    setError(null);

    const bridge = new PluginBridgeHost({
      pluginId,
      iframe,
      apiBase,
      theme: getThemePref(),
      locale: i18n.language,
      onNotification: handleNotification,
      onNavigate: handleNavigate,
    });
    bridgeRef.current = bridge;

    let slowTimer: ReturnType<typeof setTimeout> | null = null;

    const onBridgeReady = (e: MessageEvent) => {
      if (e.source !== iframe.contentWindow) return;
      const d = e.data;
      if (d && d.__akita_bridge && (d.type === "bridge:ready" || d.type === "bridge:handshake")) {
        connectedRef.current = true;
        if (slowTimer) { clearTimeout(slowTimer); slowTimer = null; }
        setLoading(false);
        setSlow(false);
        setError(null);
      }
    };
    window.addEventListener("message", onBridgeReady);

    slowTimer = setTimeout(() => {
      // Only show "slow" hint if neither bridge handshake nor iframe.onLoad has resolved.
      if (!connectedRef.current && !iframeLoadedRef.current) {
        setSlow(true);
      }
    }, BRIDGE_SLOW_MS);

    const onTheme = () => bridge.sendThemeChange(getThemePref());
    window.addEventListener(THEME_CHANGE_EVENT, onTheme);

    return () => {
      if (slowTimer) clearTimeout(slowTimer);
      window.removeEventListener("message", onBridgeReady);
      window.removeEventListener(THEME_CHANGE_EVENT, onTheme);
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, [pluginId, apiBase]);

  useEffect(() => {
    bridgeRef.current?.sendLocaleChange(i18n.language);
  }, [i18n.language]);

  const cacheBust = useMemo(() => Date.now(), [pluginId]);
  const pluginUiUrl = `${apiBase}/api/plugins/${pluginId}/ui/?_v=${cacheBust}`;

  const handleIframeLoad = useCallback(() => {
    // Network-layer load complete. Cross-origin iframes cannot tell us the
    // HTTP status, so this is a fallback to dismiss the loading overlay
    // even when the plugin UI does not implement the bridge handshake.
    iframeLoadedRef.current = true;
    setLoading(false);
    setSlow(false);
  }, []);

  const handleIframeError = useCallback(() => {
    setError(t("pluginApp.loadFailed", "Failed to load plugin UI"));
  }, [t]);

  const displayTitle = meta.title || pluginId;

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100%", minHeight: 0, position: "relative" }}>
      {loading && !error && (
        <div style={{
          position: "absolute", inset: 0, zIndex: 10,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "var(--bg, #fff)",
        }}>
          <div style={{ textAlign: "center", maxWidth: 360, padding: 24 }}>
            {meta.iconUrl ? (
              <img
                src={meta.iconUrl}
                alt=""
                style={{ width: 48, height: 48, borderRadius: 8, marginBottom: 16, objectFit: "cover" }}
              />
            ) : null}
            <div className="spinner" style={{ width: 44, height: 44, margin: "0 auto 16px" }} />
            <div style={{ fontSize: 15, fontWeight: 500, color: "var(--text, #1e293b)" }}>
              {t("pluginApp.loadingTitle", "正在加载 {{name}}…", { name: displayTitle })}
            </div>
            {slow && (
              <div style={{ marginTop: 10, fontSize: 12, color: "var(--text-muted, #94a3b8)" }}>
                {t("pluginApp.loadingSlow", "插件启动较慢，请稍候…")}
              </div>
            )}
          </div>
        </div>
      )}
      {error && (
        <div style={{
          position: "absolute", inset: 0, zIndex: 10,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "var(--bg, #fff)",
        }}>
          <div style={{ textAlign: "center", maxWidth: 400, padding: 24 }}>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, color: "var(--text-danger, #ef4444)" }}>
              {t("pluginApp.errorTitle", "Plugin Load Error")}
            </div>
            <div style={{ fontSize: 14, color: "var(--text-muted, #94a3b8)", marginBottom: 16 }}>{error}</div>
            <button
              className="btn btnPrimary"
              onClick={() => {
                connectedRef.current = false;
                iframeLoadedRef.current = false;
                setError(null);
                setLoading(true);
                setSlow(false);
                if (iframeRef.current) {
                  iframeRef.current.src = pluginUiUrl;
                }
              }}
            >
              {t("pluginApp.retry", "Retry")}
            </button>
          </div>
        </div>
      )}
      <iframe
        ref={iframeRef}
        src={pluginUiUrl}
        sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
        onLoad={handleIframeLoad}
        onError={handleIframeError}
        style={{
          flex: 1, border: "none", width: "100%", height: "100%",
          borderRadius: 8, background: "var(--bg, #fff)",
        }}
        title={`Plugin: ${displayTitle}`}
      />
    </div>
  );
}
