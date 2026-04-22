/**
 * OpenAkita Plugin UI Kit — error-coach.js
 *
 * Renders a RenderedError dict (from Python ErrorCoach.render().to_dict())
 * into the CapCut 3-part layout:
 *   - Why does it happen
 *   - What to do (prefixed by an arrow via CSS)
 *   - Tip (optional; prefixed by a pin SVG via CSS)
 *
 * Usage:
 *   const rendered = await fetch(`${OpenAkita.meta.apiBase}/last-error`).then(r=>r.json());
 *   ErrorCoach.mount('#errorBox', rendered, { onRetry: () => doRetry() });
 */
(function () {
  if (typeof window === "undefined") return;

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function render(rendered, opts) {
    rendered = rendered || {};
    opts = opts || {};
    const sev = rendered.severity || "error";
    const cls = sev === "warning" ? "oa-error-coach warning" : "oa-error-coach";
    const tip = rendered.tip ? `<div class="tip">${escapeHtml(rendered.tip)}</div>` : "";
    const retryBtn = (rendered.retryable && opts.onRetry)
      ? `<button class="oa-btn oa-btn-primary" data-retry="1" style="margin-top:10px">重试</button>`
      : "";

    return `
      <div class="${cls}">
        <div class="cause">${escapeHtml(rendered.cause_category || "未知错误")}</div>
        <div class="why">${escapeHtml(rendered.problem || "(无 problem 文本)")}</div>
        <div class="what">${escapeHtml(rendered.next_step || "(无 next_step 文本)")}</div>
        ${tip}
        ${retryBtn}
      </div>
    `;
  }

  function mount(target, rendered, opts) {
    const el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) return null;
    el.innerHTML = render(rendered, opts);
    if (opts && typeof opts.onRetry === "function") {
      const btn = el.querySelector("[data-retry]");
      if (btn) btn.addEventListener("click", opts.onRetry);
    }
    return el;
  }

  function unmount(target) {
    const el = typeof target === "string" ? document.querySelector(target) : target;
    if (el) el.innerHTML = "";
  }

  window.ErrorCoach = { render, mount, unmount };
})();
