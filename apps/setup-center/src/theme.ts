export type Theme = "light" | "dark" | "system" | "daltonized-light" | "daltonized-dark" | "high-contrast";

const RESOLVED_THEMES: Record<string, string> = {
  light: "light",
  dark: "dark",
  "daltonized-light": "daltonized-light",
  "daltonized-dark": "daltonized-dark",
  "high-contrast": "high-contrast",
};

function resolveTheme(theme: Theme): string {
  if (theme === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return RESOLVED_THEMES[theme] || theme;
}

/** Tauri native themes only support light/dark; map extended themes to the nearest. */
function toNativeTheme(theme: Theme): "light" | "dark" | null {
  if (theme === "system") return null;
  if (theme === "daltonized-light" || theme === "light") return "light";
  return "dark";
}

export function initTheme() {
  const pref = (localStorage.getItem("openakita-theme-pref") as Theme) || "system";
  applyTheme(pref);

  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const listener = (e: MediaQueryListEvent) => {
    const currentPref = localStorage.getItem("openakita-theme-pref") as Theme | null;
    if (!currentPref || currentPref === "system") {
      document.documentElement.setAttribute("data-theme", e.matches ? "dark" : "light");
    }
  };

  if (mediaQuery.addEventListener) {
    mediaQuery.addEventListener("change", listener);
  } else if (mediaQuery.addListener) {
    mediaQuery.addListener(listener);
  }
}

export function applyTheme(theme: Theme) {
  const resolved = resolveTheme(theme);
  document.documentElement.setAttribute("data-theme", resolved);

  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    import("@tauri-apps/api/window").then(({ getCurrentWindow }) => {
      const win = getCurrentWindow();
      win.setTheme(toNativeTheme(theme));
    }).catch(() => {});
  }
}

export const THEME_CHANGE_EVENT = "openakita-theme-change";

let _previewBackup: Theme | null = null;

export function setThemePref(theme: Theme) {
  _previewBackup = null;
  localStorage.setItem("openakita-theme-pref", theme);
  applyTheme(theme);
  window.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT, { detail: theme }));
}

export function getThemePref(): Theme {
  return (localStorage.getItem("openakita-theme-pref") as Theme) || "system";
}

/** Apply a theme temporarily for live preview without persisting. */
export function previewTheme(theme: Theme) {
  if (!_previewBackup) {
    _previewBackup = getThemePref();
  }
  applyTheme(theme);
}

/** Revert to the persisted theme (cancel preview). */
export function cancelPreview() {
  if (_previewBackup) {
    applyTheme(_previewBackup);
    _previewBackup = null;
  }
}

export const THEME_OPTIONS: { value: Theme; label: string; group: string }[] = [
  { value: "system", label: "跟随系统", group: "standard" },
  { value: "light", label: "浅色", group: "standard" },
  { value: "dark", label: "深色", group: "standard" },
  { value: "daltonized-light", label: "色盲友好 (浅)", group: "accessibility" },
  { value: "daltonized-dark", label: "色盲友好 (深)", group: "accessibility" },
  { value: "high-contrast", label: "高对比度", group: "accessibility" },
];
