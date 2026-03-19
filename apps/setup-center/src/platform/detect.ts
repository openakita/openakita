// Runtime platform detection constants.
// Extracted to a separate file to avoid circular dependencies.

export const IS_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const IS_CAPACITOR =
  typeof window !== "undefined" &&
  "Capacitor" in window &&
  !IS_TAURI;

export const IS_WEB = !IS_TAURI && !IS_CAPACITOR;

/** Mobile browser (Safari / Chrome on iOS / Android).
 *  These browsers aggressively suspend background tabs and kill HTTP connections,
 *  behaving like native WebViews rather than desktop browsers. */
export const IS_MOBILE_BROWSER: boolean = IS_WEB && (() => {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (/Mobile|Android|iPhone|iPod|Opera Mini|IEMobile/i.test(ua)) return true;
  // iPadOS 13+ sends a desktop-class UA; detect via touch + Mac platform
  if (/Macintosh/i.test(ua) && navigator.maxTouchPoints > 1) return true;
  return false;
})();

/** Web interface accessed from localhost — backend authenticates by IP, no tokens needed. */
export const IS_LOCAL_WEB: boolean = IS_WEB && (() => {
  try {
    const h = window.location.hostname;
    return h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "[::1]";
  } catch {
    return false;
  }
})();
