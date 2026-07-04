/**
 * StaleBundleBanner — sticky "新版本可用，请刷新页面" banner.
 *
 * P-RC-2 commit P2.8 mitigation for Phase 7 of the original
 * revamp plan (red-prompt cache issues): the SPA polls
 * ``GET /api/build-info`` every 60 s and compares the running
 * backend's ``build_id`` with this bundle's compile-time
 * ``__BUILD_ID__``. A drift means the operator is staring at a
 * cached HTML/JS bundle while the backend has already moved on,
 * which is the classic root cause of "the prompt looks wrong"
 * complaints we saw during Phase 7. Surface it loudly so the
 * operator can hit Reload before they file a bug.
 *
 * Behaviour:
 *
 * * Polls every ``pollMs`` (default 60 000 ms) starting after
 *   one initial probe ~5 s after mount (so we don't spam
 *   /api/build-info during the boot storm).
 * * Hidden until a drift is observed; after the first drift,
 *   the banner sticks until the user hits Reload (we never
 *   auto-clear because the drift is genuine).
 * * Network errors are tolerated silently — the banner only
 *   shows on a confirmed mismatch.
 *
 * The component is intentionally framework-light (plain CSS
 * inline styles, no shadcn imports) so it can render even
 * before the rest of the app boots.
 *
 * **Dev-sentinel short-circuit** (smoke-banner fix):
 * ``vite.config.ts`` falls back to ``dev-<timestamp>`` when
 * ``VITE_BUILD_ID`` is not set (i.e. local ``npm run dev``).
 * The backend's ``/api/build-info`` returns the ``openakita``
 * package version in that mode, so the comparison would
 * permanently mismatch and the banner would lock on. We detect
 * the ``dev-`` prefix on the embedded bundle id and skip
 * polling entirely; CI/production builds set an explicit
 * ``VITE_BUILD_ID`` (no ``dev-`` prefix) and keep the full
 * stale-bundle detection active.
 */

import { useEffect, useRef, useState } from "react";

export interface StaleBundleBannerProps {
  /** Backend base URL ("" = same origin). */
  apiBase?: string;
  /** Override the embedded ``__BUILD_ID__``; useful for tests. */
  bundleId?: string;
  /** Poll interval in ms. */
  pollMs?: number;
  /** Initial delay before first poll. */
  initialDelayMs?: number;
  /** Replace the global ``fetch`` (tests inject a stub here). */
  fetchImpl?: typeof fetch;
}

interface BuildInfo {
  build_id?: string;
}

export function StaleBundleBanner({
  apiBase = "",
  bundleId,
  pollMs = 60_000,
  initialDelayMs = 5_000,
  fetchImpl,
}: StaleBundleBannerProps = {}) {
  const myId = bundleId ?? __BUILD_ID__;
  const [stale, setStale] = useState(false);
  const stickyRef = useRef(false);

  useEffect(() => {
    // smoke-banner fix: when ``__BUILD_ID__`` is the
    // ``dev-<timestamp>`` sentinel emitted by vite.config.ts
    // (i.e. local ``npm run dev`` without VITE_BUILD_ID), the
    // backend's package-version build_id can never match by
    // design, so suppress the banner instead of locking it on.
    if (!myId || myId.startsWith("dev-")) {
      if (typeof console !== "undefined" && console.info) {
        console.info(
          "[StaleBundleBanner] dev-sentinel bundle id detected (",
          myId,
          ") -- skipping stale-bundle poll.",
        );
      }
      return;
    }

    let cancelled = false;
    const fx = fetchImpl ?? fetch;

    const probe = async () => {
      if (cancelled || stickyRef.current) return;
      try {
        const resp = await fx(`${apiBase}/api/build-info`, {
          method: "GET",
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!resp.ok) return;
        const body = (await resp.json()) as BuildInfo;
        const remote = (body?.build_id ?? "").trim();
        if (!remote || !myId) return;
        if (remote !== myId) {
          stickyRef.current = true;
          setStale(true);
        }
      } catch {
        // network error — keep silent, try again next tick
      }
    };

    const initial = setTimeout(probe, initialDelayMs);
    const interval = setInterval(probe, pollMs);
    return () => {
      cancelled = true;
      clearTimeout(initial);
      clearInterval(interval);
    };
  }, [apiBase, myId, pollMs, initialDelayMs, fetchImpl]);

  // When the banner becomes visible, push the rest of the app down
  // so the fixed banner does not crop the underlying content. The
  // banner is sticky once shown until the user reloads, so we set
  // a CSS variable on :root that any layout can consume via
  // padding-top: var(--app-banner-height, 0). We also set
  // body.style.paddingTop directly as a belt-and-braces fallback
  // for layouts that have not yet adopted the variable.
  useEffect(() => {
    if (!stale) return;
    const root = document.documentElement;
    const body = document.body;
    const previousVar = root.style.getPropertyValue("--app-banner-height");
    const previousPad = body.style.paddingTop;
    root.style.setProperty("--app-banner-height", "44px");
    body.style.paddingTop = "44px";
    return () => {
      // Only revert if we set them; a sibling banner could have its own
      // value, but this codebase has only one StaleBundleBanner instance.
      if (previousVar) {
        root.style.setProperty("--app-banner-height", previousVar);
      } else {
        root.style.removeProperty("--app-banner-height");
      }
      body.style.paddingTop = previousPad;
    };
  }, [stale]);

  if (!stale) return null;

  return (
    <div
      data-testid="stale-bundle-banner"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 99999,
        background: "linear-gradient(135deg, #f59e0b 0%, #f97316 100%)",
        color: "#fff",
        padding: "10px 16px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        fontSize: 14,
        fontWeight: 500,
        boxShadow: "0 2px 12px rgba(0,0,0,0.2)",
      }}
    >
      <span>新版本可用，请刷新页面</span>
      <button
        type="button"
        onClick={() => location.reload()}
        style={{
          background: "rgba(255,255,255,0.18)",
          color: "#fff",
          border: "1px solid rgba(255,255,255,0.4)",
          borderRadius: 6,
          padding: "4px 14px",
          fontSize: 13,
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        立即刷新
      </button>
    </div>
  );
}

export default StaleBundleBanner;
