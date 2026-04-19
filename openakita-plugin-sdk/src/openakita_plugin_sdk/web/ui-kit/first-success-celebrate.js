/**
 * OpenAkita Plugin UI Kit — first-success-celebrate.js
 *
 * Confetti + share + recommend on first successful generation per plugin.
 * (audit3 N1.8: avoid Canva's "0 second engagement after first success".)
 *
 * Usage:
 *   FirstSuccessCelebrate.maybeFire({
 *     storageKey: 'highlight-cutter:firstSuccess',
 *     title: '第一刀剪出来啦！',
 *     subtitle: '这条片子已经存到你电脑上了',
 *     onShare: () => doShare(),
 *     recommendations: [
 *       { id: 'subtitle-maker',  label: '加个字幕，更适合发微信' },
 *       { id: 'avatar-speaker',  label: '让数字人帮你配段开场白' },
 *     ],
 *     onRecommend: (id) => OpenAkita.navigate('plugin-app:' + id),
 *   });
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

  function writeStored(key, v) {
    try { localStorage.setItem(key, v); } catch (e) { /* ignore */ }
  }

  function spawnConfetti() {
    const root = document.createElement("div");
    root.className = "oa-confetti";
    const colors = ["#4f46e5", "#16a34a", "#f59e0b", "#ef4444", "#06b6d4", "#a855f7"];
    for (let i = 0; i < 60; i++) {
      const span = document.createElement("span");
      const left = Math.random() * 100;
      const dx = (Math.random() - 0.5) * 60 + "vw";
      const delay = Math.random() * 0.5;
      span.style.cssText = "left:" + left + "vw;background:" + colors[i % colors.length] +
        ";animation-delay:" + delay + "s;--dx:" + dx;
      root.appendChild(span);
    }
    document.body.appendChild(root);
    setTimeout(() => root.remove(), 2200);
  }

  function buildPanel(opts) {
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.45);" +
      "display:flex;align-items:center;justify-content:center;z-index:9001";

    const recHtml = (opts.recommendations || []).map((r) => `
      <button class="oa-btn" data-rec="${escapeHtml(r.id)}" style="justify-content:flex-start">
        ${escapeHtml(r.label)}
      </button>
    `).join("");

    const card = document.createElement("div");
    card.className = "oa-card";
    card.style.cssText = "max-width:380px;width:90%;background:var(--oa-bg);";
    card.innerHTML = `
      <h3 style="margin-bottom:4px">${escapeHtml(opts.title || "完成啦！")}</h3>
      <p>${escapeHtml(opts.subtitle || "")}</p>
      <div class="oa-row" style="margin-top:12px;gap:8px">
        <button class="oa-btn oa-btn-primary" data-act="share">分享给朋友</button>
        <button class="oa-btn" data-act="dismiss">先这样</button>
      </div>
      ${recHtml ? `<div style="margin-top:14px">
        <div style="font-size:11px;color:var(--oa-text-muted);margin-bottom:6px">下一步可以试试</div>
        <div class="oa-grid" style="gap:6px">${recHtml}</div>
      </div>` : ""}
    `;
    overlay.appendChild(card);
    return overlay;
  }

  function maybeFire(opts) {
    if (!opts || typeof opts.storageKey !== "string") return;
    if (readStored(opts.storageKey)) return;
    writeStored(opts.storageKey, String(Date.now()));

    spawnConfetti();
    const overlay = buildPanel(opts);
    overlay.addEventListener("click", (e) => {
      const recBtn = e.target && e.target.closest && e.target.closest("[data-rec]");
      if (recBtn) {
        const id = recBtn.getAttribute("data-rec");
        overlay.remove();
        if (typeof opts.onRecommend === "function") opts.onRecommend(id);
        return;
      }
      const actBtn = e.target && e.target.closest && e.target.closest("[data-act]");
      if (!actBtn) return;
      const act = actBtn.getAttribute("data-act");
      if (act === "share" && typeof opts.onShare === "function") opts.onShare();
      overlay.remove();
    });
    document.body.appendChild(overlay);
  }

  function reset(storageKey) {
    try { localStorage.removeItem(storageKey); } catch (e) { /* ignore */ }
  }

  window.FirstSuccessCelebrate = { maybeFire, reset };
})();
