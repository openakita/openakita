/**
 * OpenAkita Plugin UI Kit — i18n.js
 *
 * Tiny, dependency-free i18n helper for plugin UIs.
 *
 * Why this exists:
 *   - The host (setup-center) tells each plugin iframe the current UI locale
 *     via `bridge:init` payload and re-broadcasts changes via the custom
 *     event `openakita:locale-change` (see bootstrap.js).
 *   - Without a shared helper every plugin would re-implement: read the
 *     current locale, normalize "zh-CN" → "zh", subscribe to changes, and
 *     re-render its UI. This module does it once.
 *
 * Usage:
 *
 *   <script src="/api/plugins/_sdk/ui-kit/i18n.js"></script>
 *
 *   // 1) Register the dictionary once at boot.
 *   OpenAkitaI18n.register({
 *     zh: { "tabs.create": "创建", "tabs.tasks": "任务列表" },
 *     en: { "tabs.create": "Create", "tabs.tasks": "Tasks" },
 *   });
 *
 *   // 2) Translate a key (with optional vars: t("hello", { name: "Akita" }))
 *   OpenAkitaI18n.t("tabs.create");      // → "创建" or "Create"
 *
 *   // 3) Subscribe to locale changes (returns an unsubscribe fn).
 *   //    Use this in React with useState + useEffect to force re-render.
 *   const off = OpenAkitaI18n.onChange((locale) => { ... });
 *
 *   // 4) Read the current normalized locale ("zh" / "en" / etc.).
 *   OpenAkitaI18n.locale();
 *
 * Resolution order for t(key):
 *   1) dict[currentLocale][key]            (e.g. "zh-CN")
 *   2) dict[baseLocale][key]               (e.g. "zh")
 *   3) dict["en"][key]                     (default fallback)
 *   4) any first locale in the dict that has the key
 *   5) the key itself (helps spot missing translations)
 *
 * Idempotent: safe to load multiple times.
 */
(function () {
  if (typeof window === "undefined") return;
  if (window.OpenAkitaI18n && window.OpenAkitaI18n.__loaded) return;

  // The dictionary is intentionally a plain object that can be merged
  // multiple times so plugins (and even ui-kit modules) can each contribute
  // their own keys without clobbering each other.
  const dict = Object.create(null);
  const listeners = new Set();

  // Initial locale: prefer what the host already injected via bootstrap.js.
  // Fall back to the browser's language and finally to "zh" (project default).
  function readInitialLocale() {
    try {
      if (window.OpenAkita && window.OpenAkita.meta && window.OpenAkita.meta.locale) {
        return window.OpenAkita.meta.locale;
      }
    } catch (_e) { /* meta is a getter; ignore */ }
    if (navigator && navigator.language) return navigator.language;
    return "zh";
  }

  let currentLocale = readInitialLocale();

  function normalize(loc) {
    return typeof loc === "string" && loc ? loc : "zh";
  }
  function baseOf(loc) {
    return normalize(loc).split(/[-_]/)[0];
  }

  function setLocale(loc) {
    const next = normalize(loc);
    if (next === currentLocale) return;
    currentLocale = next;
    listeners.forEach(function (fn) {
      try { fn(currentLocale); } catch (_e) { /* listener errors are isolated */ }
    });
  }

  function register(dictionary) {
    if (!dictionary || typeof dictionary !== "object") return;
    Object.keys(dictionary).forEach(function (locale) {
      const entries = dictionary[locale];
      if (!entries || typeof entries !== "object") return;
      if (!dict[locale]) dict[locale] = Object.create(null);
      Object.keys(entries).forEach(function (k) { dict[locale][k] = entries[k]; });
    });
  }

  function lookup(key) {
    const loc = currentLocale;
    const base = baseOf(loc);
    if (dict[loc] && dict[loc][key] != null) return dict[loc][key];
    if (dict[base] && dict[base][key] != null) return dict[base][key];
    if (dict.en && dict.en[key] != null) return dict.en[key];
    const locs = Object.keys(dict);
    for (let i = 0; i < locs.length; i++) {
      if (dict[locs[i]][key] != null) return dict[locs[i]][key];
    }
    return null;
  }

  /**
   * Translate a key with optional interpolation.
   *   t("hello", { name: "Akita" })  →  template "Hi {name}"  →  "Hi Akita"
   * Missing keys return the key itself so they're visible in the UI and
   * easy to grep for during development.
   */
  function t(key, vars) {
    const tpl = lookup(key);
    if (tpl == null) return key;
    if (!vars) return tpl;
    return String(tpl).replace(/\{(\w+)\}/g, function (_m, name) {
      return vars[name] != null ? String(vars[name]) : "{" + name + "}";
    });
  }

  function onChange(fn) {
    if (typeof fn !== "function") return function () {};
    listeners.add(fn);
    return function () { listeners.delete(fn); };
  }

  // Subscribe to host → iframe locale broadcasts. bootstrap.js dispatches
  // this exact custom event whenever the host sends bridge:locale-change.
  window.addEventListener("openakita:locale-change", function (e) {
    const loc = e && e.detail && e.detail.locale;
    if (loc) setLocale(loc);
  });

  // Some plugin code paths (e.g. when the bridge handshake completes after
  // i18n.js loads) update window.OpenAkita.meta.locale without firing the
  // event. Poll once on next tick so the initial render uses the right
  // locale even in that race.
  setTimeout(function () {
    try {
      if (window.OpenAkita && window.OpenAkita.meta && window.OpenAkita.meta.locale) {
        setLocale(window.OpenAkita.meta.locale);
      }
    } catch (_e) { /* ignore */ }
  }, 0);

  window.OpenAkitaI18n = {
    __loaded: true,
    register: register,
    t: t,
    locale: function () { return currentLocale; },
    setLocale: setLocale,
    onChange: onChange,
  };
})();
