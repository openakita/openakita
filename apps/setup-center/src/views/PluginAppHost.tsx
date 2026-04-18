/**
 * PluginAppHost -- renders a plugin's UI inside an iframe with
 * Bridge postMessage communication.
 *
 * Handles: loading skeleton, bridge init, theme/locale forwarding,
 * timeout error state, and cleanup on unmount.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { PluginBridgeHost } from "../lib/plugin-bridge-host";
import { getThemePref, THEME_CHANGE_EVENT } from "../theme";
import type { ViewId } from "../types";

export type PluginAppHostProps = {
  pluginId: string;
  apiBase: string;
  onViewChange?: (v: ViewId) => void;
};

const BRIDGE_TIMEOUT_MS = 30_000;

export default function PluginAppHost({ pluginId, apiBase, onViewChange }: PluginAppHostProps) {
  const { t, i18n } = useTranslation();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<PluginBridgeHost | null>(null);
  const connectedRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handleNotification = useCallback((opts: { title: string; body: string; type?: string }) => {
    if (opts.type === "error") toast.error(opts.body);
    else if (opts.type === "warning") toast.warning(opts.body);
    else toast.success(opts.body);
  }, []);

  const handleNavigate = useCallback((viewId: string) => {
    if (onViewChange) onViewChange(viewId as ViewId);
  }, [onViewChange]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;

    connectedRef.current = false;

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

    let timer: ReturnType<typeof setTimeout> | null = null;

    const onBridgeReady = (e: MessageEvent) => {
      if (e.source !== iframe.contentWindow) return;
      const d = e.data;
      if (d && d.__akita_bridge && (d.type === "bridge:ready" || d.type === "bridge:handshake")) {
        connectedRef.current = true;
        if (timer) { clearTimeout(timer); timer = null; }
        setLoading(false);
        setError(null);
      }
    };
    window.addEventListener("message", onBridgeReady);

    timer = setTimeout(() => {
      if (!connectedRef.current) {
        setError(t("pluginApp.loadTimeout", "Plugin UI failed to initialize within timeout"));
      }
    }, BRIDGE_TIMEOUT_MS);

    const onTheme = () => bridge.sendThemeChange(getThemePref());
    window.addEventListener(THEME_CHANGE_EVENT, onTheme);

    return () => {
      if (timer) clearTimeout(timer);
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

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100%", minHeight: 0, position: "relative" }}>
      {loading && !error && (
        <div style={{
          position: "absolute", inset: 0, zIndex: 10,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "var(--bg, #fff)",
        }}>
          <div style={{ textAlign: "center", opacity: 0.6 }}>
            <div className="spinner" style={{ width: 28, height: 28, margin: "0 auto 12px" }} />
            <div style={{ fontSize: 14 }}>{t("pluginApp.loading", "Loading plugin...")}</div>
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
                setError(null);
                setLoading(true);
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
        style={{
          flex: 1, border: "none", width: "100%", height: "100%",
          borderRadius: 8, background: "var(--bg, #fff)",
        }}
        title={`Plugin: ${pluginId}`}
      />
    </div>
  );
}
