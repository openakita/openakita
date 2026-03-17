// Runtime platform detection constants.
// Extracted to a separate file to avoid circular dependencies.

export const IS_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const IS_CAPACITOR =
  typeof window !== "undefined" &&
  "Capacitor" in window &&
  !IS_TAURI;

export const IS_WEB = !IS_TAURI && !IS_CAPACITOR;

/** Web interface accessed from localhost — backend authenticates by IP, no tokens needed. */
export const IS_LOCAL_WEB: boolean = IS_WEB && (() => {
  try {
    const h = window.location.hostname;
    return h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "[::1]";
  } catch {
    return false;
  }
})();
