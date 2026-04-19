/**
 * OpenAkita Plugin UI Kit — cost-preview.js
 *
 * Renders a CostPreview dict (from Python CostEstimator.build().to_dict())
 * as the AnyGen-style readable card:
 *
 *   ¥1.20 ~ ¥1.80
 *   ≈ 1 块奶茶钱
 *   置信度: 高    ▾ 看明细
 *
 * Usage:
 *   CostPreview.mount('#costBox', preview);
 */
(function () {
  if (typeof window === "undefined") return;

  const CONF_LABEL = { high: "高", medium: "中", low: "低" };
  const CURRENCY_SYMBOL = { CNY: "¥", USD: "$", credit: "" };

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function fmt(amount, currency) {
    const sym = CURRENCY_SYMBOL[currency] !== undefined ? CURRENCY_SYMBOL[currency] : currency + " ";
    if (amount == null) return sym + "?";
    const fixed = amount < 10 ? amount.toFixed(2) : amount.toFixed(0);
    return `${sym}${fixed}`;
  }

  function renderBreakdown(items, currency) {
    if (!items || !items.length) return "";
    const rows = items.map((b) => `
      <tr>
        <td>${escapeHtml(b.label)}</td>
        <td style="text-align:right">${escapeHtml(b.units + " " + (b.unit_label || ""))}</td>
        <td style="text-align:right">${fmt(b.unit_price, currency)}</td>
        <td style="text-align:right">${fmt(b.subtotal, currency)}</td>
      </tr>
    `).join("");
    return `
      <table class="oa-cost-breakdown" style="width:100%;font-size:12px;margin-top:8px;border-collapse:collapse">
        <thead><tr style="text-align:left;color:var(--oa-text-muted)">
          <th>项目</th><th style="text-align:right">数量</th>
          <th style="text-align:right">单价</th><th style="text-align:right">小计</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function render(preview) {
    preview = preview || {};
    const cur = preview.currency || "CNY";
    const range = preview.low === preview.high
      ? fmt(preview.high, cur)
      : `${fmt(preview.low, cur)} ~ ${fmt(preview.high, cur)}`;
    const conf = preview.confidence || "medium";
    const human = preview.human_label ? `<div class="human">${escapeHtml(preview.human_label)}</div>` : "";
    const notes = (preview.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
    const notesBlock = notes ? `<ul style="margin:6px 0 0;padding-left:18px;font-size:11px;color:var(--oa-text-muted)">${notes}</ul>` : "";

    return `
      <div class="oa-cost">
        <div class="range">${range}</div>
        ${human}
        <span class="conf ${conf}">置信度 ${escapeHtml(CONF_LABEL[conf] || conf)}</span>
        <details style="margin-top:8px">
          <summary style="font-size:12px;color:var(--oa-text-muted);cursor:pointer">看明细</summary>
          ${renderBreakdown(preview.breakdown || [], cur)}
          ${notesBlock}
        </details>
      </div>
    `;
  }

  function mount(target, preview) {
    const el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) return null;
    el.innerHTML = render(preview);
    return el;
  }

  window.CostPreview = { render, mount };
})();
