/**
 * OpenAkita Plugin UI Kit — event-helpers.js
 *
 * Adds friendly event listeners on top of the bootstrap.js bridge.
 *
 * Why this exists: the host always namespaces broadcast_ui_event with
 *   "plugin:<plugin_id>:<event_type>"
 * but plugin authors want to listen for the bare "<event_type>".
 * This helper strips the prefix automatically (audit3 fix; backwards
 * compatible — full-prefixed listeners still work).
 *
 * Usage in plugin HTML:
 *
 *   <script src="/api/plugins/_sdk/bootstrap.js"></script>
 *   <script src="/api/plugins/_sdk/ui-kit/event-helpers.js"></script>
 *   <script>
 *     OpenAkita.onEvent('task_updated', (data) => { ... });
 *     OpenAkita.onEvent('plugin:other-plugin:something', (data) => { ... }); // also OK
 *   </script>
 */
(function () {
  if (typeof window === "undefined" || !window.OpenAkita) return;
  if (window.OpenAkita.onEvent) return; // already installed

  const bare = Object.create(null);  // bareName -> [fn]
  const full = Object.create(null);  // fullName -> [fn]

  function stripPrefix(type, pluginId) {
    if (typeof type !== "string" || !type.startsWith("plugin:")) return null;
    // Format: plugin:<id>:<event>
    const rest = type.slice("plugin:".length);
    const sep = rest.indexOf(":");
    if (sep < 0) return null;
    const pid = rest.slice(0, sep);
    const evt = rest.slice(sep + 1);
    if (pluginId && pid && pid !== pluginId) return null; // not for us
    return { pluginId: pid, eventType: evt };
  }

  function dispatch(payload) {
    if (!payload || typeof payload !== "object") return;
    const type = payload.type || payload.eventType;
    const data = payload.data !== undefined ? payload.data : payload;
    if (typeof type !== "string") return;

    // 1) full-name listeners
    const fHandlers = full[type];
    if (fHandlers && fHandlers.length) {
      for (const fn of fHandlers) {
        try { fn(data, { fullType: type }); } catch (e) { /* ignore */ }
      }
    }
    // 2) prefix-stripped bare listeners (only when prefix matches our plugin)
    const meta = OpenAkita.meta || {};
    const stripped = stripPrefix(type, meta.pluginId);
    if (stripped) {
      const bHandlers = bare[stripped.eventType];
      if (bHandlers && bHandlers.length) {
        for (const fn of bHandlers) {
          try { fn(data, { fullType: type, pluginId: stripped.pluginId }); }
          catch (e) { /* ignore */ }
        }
      }
    }
  }

  window.addEventListener("openakita:event", function (e) {
    dispatch(e && e.detail);
  });

  /**
   * onEvent(eventType, fn) — register a listener.
   *
   * If `eventType` already starts with "plugin:" it is matched verbatim.
   * Otherwise it is treated as a *bare* name and the prefix is stripped
   * before dispatch (so plugin code is namespace-agnostic).
   */
  window.OpenAkita.onEvent = function (eventType, fn) {
    if (typeof eventType !== "string" || typeof fn !== "function") return () => {};
    const bucket = eventType.startsWith("plugin:") ? full : bare;
    bucket[eventType] = bucket[eventType] || [];
    bucket[eventType].push(fn);
    return function off() {
      bucket[eventType] = (bucket[eventType] || []).filter((h) => h !== fn);
    };
  };

  /** offEvent(eventType, fn?) — remove one or all listeners for a type. */
  window.OpenAkita.offEvent = function (eventType, fn) {
    if (typeof eventType !== "string") return;
    const bucket = eventType.startsWith("plugin:") ? full : bare;
    if (!fn) { delete bucket[eventType]; return; }
    bucket[eventType] = (bucket[eventType] || []).filter((h) => h !== fn);
  };

  // Expose helper for tests / advanced uses.
  window.OpenAkita.__stripPluginEventPrefix = stripPrefix;
})();
