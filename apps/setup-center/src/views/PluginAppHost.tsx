/**
 * PluginAppHost -- renders a plugin's UI inside an iframe with
 * Bridge postMessage communication.
 *
 * Handles: loading skeleton, bridge init, theme/locale forwarding,
 * timeout soft-warning, hard error on iframe load failure, and full
 * cleanup / state reset on plugin switch.
 *
 * Loading-overlay dismissal priority (most accurate first):
 *   1) bridge:render-ready  -- plugin called window.OpenAkita.ready() (best;
 *      fires AFTER first React/Vue render, i.e. when content is on screen)
 *   2) iframe.onLoad + ONLOAD_FALLBACK_MS  -- network done, give SPA bootstrap
 *      a brief grace period (covers Babel-standalone / framework mount delay
 *      for plugins that did NOT call OpenAkita.ready())
 *   3) HARD_TIMEOUT_MS  -- absolute upper bound, prevents stuck overlay
 *
 * NOTE: bridge:ready / bridge:handshake (sent by bootstrap.js at
 * DOMContentLoaded) are intentionally NOT used to dismiss the overlay.
 * For Babel-compiled SPAs, DOMContentLoaded fires BEFORE React renders the
 * first frame, so dismissing on handshake would re-introduce the original
 * "loading flash then blank screen" bug. We still mark `connected` on
 * handshake so other bridge messages are recognized.
 *
 * A separate BRIDGE_SLOW_MS triggers a non-blocking "loading is slow" hint.
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
/** After iframe.onLoad fires, give the in-page bootstrap (Babel, React mount, etc.) this long before forcibly dismissing the overlay. */
const ONLOAD_FALLBACK_MS = 1_500;
/** Absolute upper bound. After this, dismiss the overlay no matter what. */
const HARD_TIMEOUT_MS = 15_000;

export default function PluginAppHost({ pluginId, apiBase, onViewChange }: PluginAppHostProps) {
  const { t, i18n } = useTranslation();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<PluginBridgeHost | null>(null);
  const connectedRef = useRef(false);
  const iframeLoadedRef = useRef(false);
  const overlayDismissedRef = useRef(false);
  const onloadFallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dismissOverlayRef = useRef<(() => void) | null>(null);
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
    overlayDismissedRef.current = false;
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
    let hardTimer: ReturnType<typeof setTimeout> | null = null;
    // Note: the onload-fallback timer is owned by `onloadFallbackTimerRef`,
    // not a local let, because it is created from `handleIframeLoad`
    // (component-scope useCallback) which cannot close over effect-local
    // variables. Both this effect's cleanup and `dismissOverlay` clear it.

    const clearOnloadFallbackTimer = () => {
      if (onloadFallbackTimerRef.current) {
        clearTimeout(onloadFallbackTimerRef.current);
        onloadFallbackTimerRef.current = null;
      }
    };

    const dismissOverlay = () => {
      if (overlayDismissedRef.current) return;
      overlayDismissedRef.current = true;
      if (slowTimer) { clearTimeout(slowTimer); slowTimer = null; }
      if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
      clearOnloadFallbackTimer();
      setLoading(false);
      setSlow(false);
      setError(null);
    };
    dismissOverlayRef.current = dismissOverlay;

    const onBridgeReady = (e: MessageEvent) => {
      if (e.source !== iframe.contentWindow) return;
      const d = e.data;
      if (!d || d.__akita_bridge !== true) return;
      // Mark "bridge connected" on either handshake message — but do NOT
      // dismiss the overlay yet, because handshake fires at DOMContentLoaded,
      // i.e. before SPAs render their first frame.
      if (d.type === "bridge:ready" || d.type === "bridge:handshake") {
        connectedRef.current = true;
        return;
      }
      // The ONLY message that signals "plugin UI is visually ready".
      // Dismisses the loading overlay immediately.
      if (d.type === "bridge:render-ready") {
        connectedRef.current = true;
        dismissOverlay();
      }
    };
    window.addEventListener("message", onBridgeReady);

    slowTimer = setTimeout(() => {
      if (!overlayDismissedRef.current) setSlow(true);
    }, BRIDGE_SLOW_MS);

    hardTimer = setTimeout(() => {
      // Absolute fallback: even if iframe.onLoad never fires (e.g. plugin
      // does an infinite redirect or document never finishes loading), don't
      // leave the user stuck on the overlay forever.
      dismissOverlay();
    }, HARD_TIMEOUT_MS);

    const onTheme = () => bridge.sendThemeChange(getThemePref());
    window.addEventListener(THEME_CHANGE_EVENT, onTheme);

    return () => {
      if (slowTimer) clearTimeout(slowTimer);
      if (hardTimer) clearTimeout(hardTimer);
      // CRITICAL: the onload-fallback timer is on a ref (set by
      // handleIframeLoad). Without clearing it here, switching to a different
      // plugin could let an in-flight timer fire later and call
      // dismissOverlayRef.current() — which by then points at the NEW
      // plugin's effect and would prematurely hide its loading overlay.
      clearOnloadFallbackTimer();
      window.removeEventListener("message", onBridgeReady);
      window.removeEventListener(THEME_CHANGE_EVENT, onTheme);
      bridge.dispose();
      bridgeRef.current = null;
      dismissOverlayRef.current = null;
    };
  }, [pluginId, apiBase]);

  useEffect(() => {
    bridgeRef.current?.sendLocaleChange(i18n.language);
  }, [i18n.language]);

  // cacheBust controls the iframe's `?_v=` query string. We deliberately
  // memoize it (instead of recomputing on every render) so that incidental
  // re-renders (loading/slow/error state, theme changes) do NOT trigger a
  // full iframe reload mid-session.
  //
  // It changes when:
  //   - pluginId changes (user switches plugin) -- always
  //   - `reloadTick` is bumped -- via the dev-only "force reload" hotkey or
  //     any future explicit "reload plugin" action
  //
  // Dev-only ergonomics (do NOT enable in production):
  //   - Alt+Shift+R while focus is in setup-center: force-reload the active
  //     plugin iframe with a fresh cacheBust. Shipping plugin authors a
  //     reliable refresh shortcut means they never have to switch plugins or
  //     hard-reload the whole shell to see updates to their HTML / CSS / JS.
  //
  // Note: an earlier version also reloaded on window `focus` to catch the
  // "save in editor → alt-tab back to browser" workflow. That turned out to
  // be far too aggressive — clicking inside the plugin iframe moves focus
  // INTO the iframe, and the very next click outside it (e.g. the host
  // shell's blank area or scrollbar) fires `focus` on the parent window and
  // would tear the iframe down mid-interaction. Manual Alt+Shift+R is more
  // predictable; we can revisit with a smarter heuristic later.
  const [reloadTick, setReloadTick] = useState(0);
  const cacheBust = useMemo(() => Date.now(), [pluginId, reloadTick]);
  const pluginUiUrl = `${apiBase}/api/plugins/${pluginId}/ui/?_v=${cacheBust}`;

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && e.shiftKey && (e.key === "R" || e.key === "r")) {
        e.preventDefault();
        setReloadTick((t) => t + 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // Listen for explicit plugin-reload events fired by PluginManagerView's
  // "Reload" button (and any future programmatic trigger). When a reload
  // targets the currently-shown plugin, force the iframe to refetch its
  // bundle (back-end Python is hot-reloaded by reload_plugin, but the
  // browser still has the OLD HTML/JS/CSS cached against the previous
  // ?_v= query string — without bumping cacheBust here, the user must
  // hard-refresh or remove+install to see UI changes).
  //
  // Active in BOTH dev and production: this is an explicit user action,
  // not a dev-only ergonomic shortcut.
  useEffect(() => {
    const onPluginReloaded = (e: Event) => {
      const detail = (e as CustomEvent).detail as { pluginId?: string } | undefined;
      // Targeted reload: only react if it's for the currently-shown plugin.
      // Broadcast reload (no detail / no pluginId): always react.
      if (detail && detail.pluginId && detail.pluginId !== pluginId) return;
      // Reset all loading-overlay state so the user sees a fresh spinner
      // (and not stale "ready" state) while the new iframe boots.
      connectedRef.current = false;
      iframeLoadedRef.current = false;
      overlayDismissedRef.current = false;
      if (onloadFallbackTimerRef.current) {
        clearTimeout(onloadFallbackTimerRef.current);
        onloadFallbackTimerRef.current = null;
      }
      setError(null);
      setLoading(true);
      setSlow(false);
      setReloadTick((t) => t + 1);
    };
    window.addEventListener("openakita:plugin-reloaded", onPluginReloaded);
    return () => window.removeEventListener("openakita:plugin-reloaded", onPluginReloaded);
  }, [pluginId]);

  const handleIframeLoad = useCallback(() => {
    // Network-layer load complete (all standard <script src> have downloaded).
    // We do NOT dismiss the overlay immediately, because:
    //   - <script type="text/babel"> compiles AFTER load fires
    //   - SPAs (React/Vue) mount in a microtask after load
    // Instead, give the in-page bootstrap a brief grace window. The bridge
    // handshake / OpenAkita.ready() will normally fire well within it; if
    // not (e.g. plugin doesn't include bootstrap.js at all), this fallback
    // dismisses the overlay so the user is not stuck staring at a spinner
    // while the plugin is actually visible behind it.
    iframeLoadedRef.current = true;
    if (onloadFallbackTimerRef.current) clearTimeout(onloadFallbackTimerRef.current);
    onloadFallbackTimerRef.current = setTimeout(() => {
      dismissOverlayRef.current?.();
    }, ONLOAD_FALLBACK_MS);
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
              {t("pluginApp.loadingTitle", "Loading {{name}}…", { name: displayTitle })}
            </div>
            {slow && (
              <div style={{ marginTop: 10, fontSize: 12, color: "var(--text-muted, #94a3b8)" }}>
                {t("pluginApp.loadingSlow", "Plugin is taking a while to start — please wait…")}
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
                overlayDismissedRef.current = false;
                if (onloadFallbackTimerRef.current) {
                  clearTimeout(onloadFallbackTimerRef.current);
                  onloadFallbackTimerRef.current = null;
                }
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
