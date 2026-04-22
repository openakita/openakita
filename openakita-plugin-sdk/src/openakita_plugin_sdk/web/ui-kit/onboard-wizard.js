/**
 * OpenAkita Plugin UI Kit — onboard-wizard.js
 *
 * Single-question personalization (Canva style; audit3 anti-pattern fix:
 * we DO NOT do a forced 5-step tour).
 *
 * Usage:
 *   OnboardWizard.askOnce({
 *     storageKey: 'highlight-cutter:audience',
 *     question: '你主要是为了…',
 *     options: [
 *       { id: 'social', label: '发朋友圈/短视频' },
 *       { id: 'work',   label: '做工作汇报'     },
 *       { id: 'study',  label: '学习/复盘'       },
 *       { id: 'other',  label: '随便玩玩'       },
 *     ],
 *     onChoose: (id) => loadTemplatesFor(id),
 *   });
 *
 * If the user already answered before, the callback fires with the stored
 * value and no UI is shown.
 */
(function () {
  if (typeof window === "undefined") return;

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function readStored(key) {
    try { return localStorage.getItem(key) || ""; } catch (e) { return ""; }
  }

  function writeStored(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }

  function buildOverlay(opts) {
    const overlay = document.createElement("div");
    overlay.className = "oa-onboard-overlay";
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.4);" +
      "display:flex;align-items:center;justify-content:center;z-index:9000";

    const card = document.createElement("div");
    card.className = "oa-card";
    card.style.cssText = "max-width:380px;width:90%;background:var(--oa-bg);";
    card.innerHTML = `
      <h3>${escapeHtml(opts.question || "你想要…？")}</h3>
      <div class="oa-grid" style="margin-top:12px;gap:8px">
        ${(opts.options || []).map((o) => `
          <button class="oa-btn" data-id="${escapeHtml(o.id)}" style="justify-content:flex-start">
            ${escapeHtml(o.label)}
          </button>
        `).join("")}
      </div>
      <div style="margin-top:10px;text-align:right">
        <button class="oa-btn" data-id="__skip" style="font-size:12px;color:var(--oa-text-muted)">先跳过</button>
      </div>
    `;
    overlay.appendChild(card);
    return overlay;
  }

  function askOnce(opts) {
    if (!opts || typeof opts.storageKey !== "string") return;
    const stored = readStored(opts.storageKey);
    if (stored) {
      if (typeof opts.onChoose === "function") opts.onChoose(stored, { stored: true });
      return;
    }
    const overlay = buildOverlay(opts);
    overlay.addEventListener("click", (e) => {
      const btn = e.target && e.target.closest && e.target.closest("[data-id]");
      if (!btn) return;
      const id = btn.getAttribute("data-id");
      if (id && id !== "__skip") writeStored(opts.storageKey, id);
      overlay.remove();
      if (typeof opts.onChoose === "function") opts.onChoose(id === "__skip" ? "" : id, { stored: false });
    });
    document.body.appendChild(overlay);
  }

  function reset(storageKey) {
    try { localStorage.removeItem(storageKey); } catch (e) { /* ignore */ }
  }

  window.OnboardWizard = { askOnce, reset };
})();
