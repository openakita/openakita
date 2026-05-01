/**
 * OpenAkita Plugin UI Bootstrap (self-contained copy).
 */
(function () {
  if (typeof window === "undefined") return;
  if (window.OpenAkita && window.OpenAkita.__bootstrapped) return;

  var meta = { theme: "light", locale: "zh-CN", apiBase: "", pluginId: "ppt-maker" };
  var pending = Object.create(null);

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme || "light");
  }

  function send(type, payload, requestId) {
    if (window.parent === window) return;
    window.parent.postMessage(
      { __akita_bridge: true, version: 1, type: type, payload: payload, requestId: requestId },
      "*",
    );
  }

  function request(path, options) {
    options = options || {};
    var url = path;
    if (meta.apiBase && !/^https?:\/\//.test(path)) {
      url = meta.apiBase.replace(/\/$/, "") + "/" + path.replace(/^\//, "");
    }
    return fetch(url, {
      method: options.method || "GET",
      headers: Object.assign({ "Content-Type": "application/json" }, options.headers || {}),
      body: options.body ? JSON.stringify(options.body) : undefined,
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      var contentType = response.headers.get("content-type") || "";
      return contentType.indexOf("application/json") >= 0 ? response.json() : response.text();
    });
  }

  window.OpenAkita = {
    __bootstrapped: true,
    meta: meta,
    request: request,
    postMessage: send,
  };

  window.addEventListener("message", function (event) {
    var msg = event.data || {};
    if (!msg.__akita_bridge) return;
    if (msg.type === "bridge:config" || msg.type === "bridge:ready") {
      Object.assign(meta, msg.payload || {});
      applyTheme(meta.theme);
      window.dispatchEvent(new CustomEvent("openakita:ready", { detail: meta }));
    }
    if (msg.requestId && pending[msg.requestId]) {
      pending[msg.requestId](msg.payload);
      delete pending[msg.requestId];
    }
  });

  applyTheme(meta.theme);
  send("bridge:handshake", { pluginId: meta.pluginId });
})();

