/* idea-research UI bootstrap — Phase 5.
 *
 * Wires the plugin frontend to the OpenAkita host bridge:
 * - theme / locale postMessage from the host shell
 * - ``apiFetch`` helper that resolves the plugin route prefix
 *   automatically so the same bundle works whether mounted under
 *   ``/api/plugins/idea-research`` (host) or served standalone
 *   (e.g. via ``python -m http.server`` for design preview).
 * - SSE subscription helper for ``idea.task.*`` / ``idea.mdrm.*``
 *   events.
 *
 * The React workbench (index.html bottom of file) relies on these
 * globals: ``window.OpenAkita.idea_research.api`` /
 * ``.subscribe`` / ``.locale`` / ``.theme``.
 */
(function () {
  "use strict";

  var BRIDGE_READY = "bridge:ready";
  var BRIDGE_THEME = "bridge:theme-change";
  var BRIDGE_LOCALE = "bridge:locale-change";

  var state = {
    theme: detectTheme(),
    locale: detectLocale(),
    sse: null,
    plugin_id: "idea-research",
  };

  function detectTheme() {
    try {
      var saved = window.localStorage.getItem("idea_research_theme_v1");
      if (saved) return saved;
    } catch (_) {}
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return "dark";
    }
    return "light";
  }

  function detectLocale() {
    try {
      var saved = window.localStorage.getItem("idea_research_locale_v1");
      if (saved) return saved;
    } catch (_) {}
    var lang = (navigator.language || "zh").toLowerCase();
    return lang.indexOf("zh") === 0 ? "zh" : "en";
  }

  function applyTheme(theme) {
    if (!theme) return;
    state.theme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    try { window.localStorage.setItem("idea_research_theme_v1", theme); } catch (_) {}
  }

  function applyLocale(locale) {
    if (!locale) return;
    state.locale = locale;
    document.documentElement.setAttribute("lang", locale);
    try { window.localStorage.setItem("idea_research_locale_v1", locale); } catch (_) {}
  }

  function postReady() {
    try {
      window.parent && window.parent.postMessage(
        { type: BRIDGE_READY, plugin: state.plugin_id }, "*",
      );
    } catch (_) {}
  }

  function onMessage(event) {
    var data = event && event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === BRIDGE_THEME) applyTheme(data.theme);
    if (data.type === BRIDGE_LOCALE) applyLocale(data.locale);
  }

  function pluginPrefix() {
    var p = (window.location.pathname || "").replace(/\/+$/, "");
    var marker = "/api/plugins/" + state.plugin_id;
    var idx = p.indexOf(marker);
    if (idx >= 0) return p.slice(0, idx + marker.length);
    return marker; // fallback when served by the host shell at /
  }

  function joinUrl(base, path) {
    if (!path) return base;
    if (path.indexOf("http://") === 0 || path.indexOf("https://") === 0) return path;
    if (path.indexOf("/") !== 0) path = "/" + path;
    return base + path;
  }

  function buildQuery(params) {
    if (!params) return "";
    var parts = [];
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === "") return;
      if (Array.isArray(v)) {
        parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v.join(",")));
      } else {
        parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
      }
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  function apiFetch(method, path, options) {
    options = options || {};
    var base = pluginPrefix();
    var url = joinUrl(base, path) + buildQuery(options.query);
    var init = {
      method: (method || "GET").toUpperCase(),
      headers: { "Accept": "application/json" },
    };
    if (options.headers) {
      Object.keys(options.headers).forEach(function (k) { init.headers[k] = options.headers[k]; });
    }
    if (options.body !== undefined && options.body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
    }
    return fetch(url, init).then(function (resp) {
      var ct = resp.headers.get("Content-Type") || "";
      var jsonP = ct.indexOf("application/json") >= 0 ? resp.json() : resp.text();
      return jsonP.then(function (body) {
        if (!resp.ok) {
          var err = new Error(
            (body && body.detail) || (typeof body === "string" ? body : ("HTTP " + resp.status)),
          );
          err.status = resp.status;
          err.body = body;
          throw err;
        }
        return body;
      });
    });
  }

  // SSE bus — the host exposes ``GET /api/plugins/_ui-events`` with
  // ``data: { plugin: <id>, type: <event>, data: {...} }`` payloads.
  // When that endpoint is missing (eg. design preview) we silently
  // no-op so the React workbench still renders.
  function subscribe(handler) {
    if (state.sse) state.sse.close();
    var ssePath = "/api/plugins/_ui-events?plugin=" + encodeURIComponent(state.plugin_id);
    try {
      var es = new EventSource(ssePath);
      es.onmessage = function (ev) {
        try {
          var payload = JSON.parse(ev.data);
          if (!payload || (payload.plugin && payload.plugin !== state.plugin_id)) return;
          handler(payload.type || "message", payload.data || payload);
        } catch (_) {}
      };
      es.onerror = function () { /* keep retrying — browser will reconnect */ };
      state.sse = es;
      return function () { es.close(); state.sse = null; };
    } catch (_) {
      return function () {};
    }
  }

  applyTheme(state.theme);
  applyLocale(state.locale);

  window.addEventListener("message", onMessage, false);
  window.addEventListener("DOMContentLoaded", postReady, { once: true });

  window.OpenAkita = window.OpenAkita || {};
  window.OpenAkita.idea_research = {
    version: "1.0.0",
    state: state,
    api: apiFetch,
    subscribe: subscribe,
    setTheme: applyTheme,
    setLocale: applyLocale,
  };
})();
