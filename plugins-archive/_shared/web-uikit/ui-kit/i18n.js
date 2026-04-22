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
 *   // 5) Vanilla DOM helper — annotate static markup once and keep it
 *   //    in sync with locale changes:
 *   //
 *   //      <h1 data-i18n="header.title"></h1>
 *   //      <input data-i18n-placeholder="form.text" />
 *   //      <button data-i18n-title="actions.save"></button>
 *   //
 *   //    Then, after the dictionary is registered, call ONCE:
 *   //
 *   //      OpenAkitaI18n.bindDom(document.body);
 *   //
 *   //    bindDom() = applyDom(root) + auto re-apply on every locale change.
 *   //    Returns an unbind function. Safe to call multiple times.
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

  // ---------------------------------------------------------------------------
  // Built-in `bridge.*` dictionary.
  //
  // The plugin bridge layer (download / clipboard / pick-folder / …) emits
  // a small set of standard toasts that every plugin would otherwise have
  // to translate in its own dictionary. Registering them here keeps each
  // plugin's i18n surface focused on domain-specific strings.
  //
  // Plugins can still override any key by registering it again later —
  // `register()` is a merge, not a replace.
  // ---------------------------------------------------------------------------
  register({
    zh: {
      "bridge.download.ok":        "已保存到: {path}",
      "bridge.download.okFolder":  "已保存到 Downloads 文件夹",
      "bridge.download.fail":      "下载失败: {msg}",
      "bridge.download.started":   "下载已开始",
      "bridge.clipboard.ok":       "已复制到剪贴板",
      "bridge.clipboard.fail":     "复制失败",
      "bridge.pickFolder.title":   "选择文件夹",
      "bridge.unknownError":       "未知错误",
    },
    en: {
      "bridge.download.ok":        "Saved to: {path}",
      "bridge.download.okFolder":  "Saved to Downloads folder",
      "bridge.download.fail":      "Download failed: {msg}",
      "bridge.download.started":   "Download started",
      "bridge.clipboard.ok":       "Copied to clipboard",
      "bridge.clipboard.fail":     "Copy failed",
      "bridge.pickFolder.title":   "Pick a folder",
      "bridge.unknownError":       "Unknown error",
    },
  });

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

  // ---------------------------------------------------------------------------
  // Declarative DOM bindings for vanilla-JS plugins.
  //
  // The supported attributes are intentionally narrow so we never run a parser
  // on user content (no innerHTML for translated text). If a plugin really
  // needs rich markup it should compose multiple elements instead.
  //
  //   data-i18n="key"               → element.textContent
  //   data-i18n-placeholder="key"   → element.placeholder
  //   data-i18n-title="key"         → element.title
  //   data-i18n-aria-label="key"    → element.setAttribute("aria-label", ...)
  //   data-i18n-value="key"         → element.value (for <input>/<button> labels)
  //
  // Variables:
  //   data-i18n-vars='{"name":"Akita"}'  // optional, JSON-encoded
  // ---------------------------------------------------------------------------
  const ATTR_BINDINGS = [
    { sel: "[data-i18n]",              key: "i18n",          apply: function (el, v) { el.textContent = v; } },
    { sel: "[data-i18n-placeholder]",  key: "i18nPlaceholder", apply: function (el, v) { el.setAttribute("placeholder", v); } },
    { sel: "[data-i18n-title]",        key: "i18nTitle",     apply: function (el, v) { el.setAttribute("title", v); } },
    { sel: "[data-i18n-aria-label]",   key: "i18nAriaLabel", apply: function (el, v) { el.setAttribute("aria-label", v); } },
    { sel: "[data-i18n-value]",        key: "i18nValue",     apply: function (el, v) {
      // For inputs, .value is a property; for option/button textContent is more useful.
      if (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") el.value = v;
      else el.textContent = v;
    } },
  ];

  function readVars(el) {
    const raw = el.getAttribute && el.getAttribute("data-i18n-vars");
    if (!raw) return undefined;
    try { return JSON.parse(raw); } catch (_e) { return undefined; }
  }

  function applyDom(root) {
    if (!root || typeof root.querySelectorAll !== "function") {
      // Allow calling applyDom() with no args before <body> exists — silent no-op.
      return;
    }
    ATTR_BINDINGS.forEach(function (binding) {
      // Include `root` itself if it matches (querySelectorAll only finds descendants).
      const nodes = root.querySelectorAll(binding.sel);
      const list = [];
      if (root.matches && root.matches(binding.sel)) list.push(root);
      for (let i = 0; i < nodes.length; i++) list.push(nodes[i]);
      list.forEach(function (el) {
        const key = el.dataset && el.dataset[binding.key];
        if (!key) return;
        const value = t(key, readVars(el));
        try { binding.apply(el, value); } catch (_e) { /* per-element errors are isolated */ }
      });
    });
  }

  function bindDom(root) {
    const target = root || (typeof document !== "undefined" ? document.body : null);
    if (!target) return function () {};
    applyDom(target);
    const off = onChange(function () { applyDom(target); });
    return off;
  }

  window.OpenAkitaI18n = {
    __loaded: true,
    register: register,
    t: t,
    locale: function () { return currentLocale; },
    setLocale: setLocale,
    onChange: onChange,
    applyDom: applyDom,
    bindDom: bindDom,
  };
})();
