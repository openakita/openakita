/**
 * OpenAkita Plugin UI Bootstrap (zero-dependency, ~3KB)
 *
 * Plugin authors include this once in their UI's <head>:
 *   <script src="/api/plugins/_sdk/bootstrap.js"></script>
 *
 * It performs the bridge:ready / bridge:handshake handshake with the host,
 * receives theme / locale / apiBase / pluginId, and exposes a tiny
 * helper API at window.OpenAkita for plugin code to use.
 *
 * Idempotent: safe to load multiple times.
 */
(function () {
  if (typeof window === "undefined") return;
  // Not inside an iframe: nothing to do (and avoid posting to ourselves).
  if (window.parent === window) return;
  if (window.OpenAkita && window.OpenAkita.__bootstrapped) return;

  var BRIDGE_VERSION = 1;
  var meta = { theme: "light", locale: "zh-CN", apiBase: "", pluginId: "" };
  var pending = Object.create(null);

  function send(type, payload, requestId) {
    try {
      var msg = { __akita_bridge: true, version: BRIDGE_VERSION, type: type };
      if (payload !== undefined) msg.payload = payload;
      if (requestId !== undefined) msg.requestId = requestId;
      window.parent.postMessage(msg, "*");
    } catch (_) { /* ignore */ }
  }

  function applyTheme(t) {
    try { document.documentElement.setAttribute("data-theme", t || "light"); } catch (_) {}
  }

  function dispatch(name, detail) {
    try { window.dispatchEvent(new CustomEvent(name, { detail: detail })); } catch (_) {}
  }

  function genRequestId() {
    return "r" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent) return;
    var d = event.data;
    if (!d || d.__akita_bridge !== true || typeof d.type !== "string") return;

    switch (d.type) {
      case "bridge:init":
      case "bridge:handshake-ack": {
        var p = d.payload || {};
        if (p.theme) meta.theme = p.theme;
        if (p.locale) meta.locale = p.locale;
        if (typeof p.apiBase === "string") meta.apiBase = p.apiBase;
        if (typeof p.pluginId === "string") meta.pluginId = p.pluginId;
        applyTheme(meta.theme);
        if (!window.OpenAkita.__ready) {
          window.OpenAkita.__ready = true;
          dispatch("openakita:ready", Object.assign({}, meta));
        }
        break;
      }
      case "bridge:theme-change": {
        var t = (d.payload && d.payload.theme) || "light";
        meta.theme = t;
        applyTheme(t);
        dispatch("openakita:theme-change", { theme: t });
        break;
      }
      case "bridge:locale-change": {
        var l = (d.payload && d.payload.locale) || "zh-CN";
        meta.locale = l;
        dispatch("openakita:locale-change", { locale: l });
        break;
      }
      case "bridge:event": {
        dispatch("openakita:event", d.payload || {});
        break;
      }
      case "bridge:api-response":
      case "bridge:download-ack":
      case "bridge:show-in-folder-ack":
      case "bridge:pick-folder-ack":
      case "bridge:clipboard-ack": {
        if (d.requestId && pending[d.requestId]) {
          var resolver = pending[d.requestId];
          delete pending[d.requestId];
          try { resolver(d.payload); } catch (_) {}
        }
        break;
      }
      default:
        break;
    }
  });

  function request(type, payload) {
    return new Promise(function (resolve) {
      var rid = genRequestId();
      pending[rid] = resolve;
      send(type, payload, rid);
      setTimeout(function () {
        if (pending[rid]) {
          delete pending[rid];
          resolve({ ok: false, error: "timeout" });
        }
      }, 30000);
    });
  }

  window.OpenAkita = {
    __bootstrapped: true,
    __ready: false,
    bridgeVersion: BRIDGE_VERSION,
    get meta() { return Object.assign({}, meta); },
    api: function (method, path, body) {
      return request("bridge:api-request", { method: method, path: path, body: body });
    },
    notify: function (opts) { send("bridge:notification", opts || {}); },
    navigate: function (viewId) { send("bridge:navigate", { viewId: viewId }); },
    download: function (url, filename) {
      return request("bridge:download", { url: url, filename: filename });
    },
    showInFolder: function (path) {
      return request("bridge:show-in-folder", { path: path });
    },
    pickFolder: function (title) {
      return request("bridge:pick-folder", { title: title });
    },
    clipboard: function (text) {
      return request("bridge:clipboard", { text: text });
    },
    onReady: function (cb) {
      if (window.OpenAkita.__ready) {
        try { cb(Object.assign({}, meta)); } catch (_) {}
      } else {
        window.addEventListener("openakita:ready", function (e) { cb(e.detail); }, { once: true });
      }
    },
  };

  function handshake() {
    send("bridge:ready");
    send("bridge:handshake");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", handshake, { once: true });
  } else {
    handshake();
  }
})();
