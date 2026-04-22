/**
 * OpenAkita Plugin UI Kit — task-panel.js
 *
 * A compact task list with status pills, ETA, and a cancel button.
 * Backend should expose:
 *   GET    {apiBase}/tasks?status=&limit=     -> [{id, status, prompt, created_at, eta_sec?, ...}]
 *   POST   {apiBase}/tasks/{id}/cancel
 *
 * Plus optional realtime via OpenAkita.onEvent('task_updated', ...)
 *
 * Usage:
 *   const panel = new TaskPanel({ root: '#tasks', apiBase: OpenAkita.meta.apiBase });
 *   panel.start();
 *   OpenAkita.onEvent('task_updated', () => panel.refresh());
 */
(function () {
  if (typeof window === "undefined") return;

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function fmtTime(ts) {
    if (!ts) return "-";
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function renderRow(t) {
    const status = (t.status || "pending").toLowerCase();
    const cancelable = !["succeeded", "failed", "cancelled"].includes(status);
    const eta = t.eta_sec != null ? `约 ${Math.max(1, Math.round(t.eta_sec))} 秒` : "";
    const elapsed = t.elapsed_sec != null ? `已运行 ${Math.round(t.elapsed_sec)} 秒` : "";
    const queue = t.queue_position != null ? `排队第 ${t.queue_position} 位` : "";
    const ts = (t.title || t.prompt || t.id || "").toString();
    const subtitle = [elapsed, queue, eta].filter(Boolean).join(" · ");
    const cancelBtn = cancelable ? `<button class="oa-btn" data-cancel="${escapeHtml(t.id)}">取消</button>` : "";

    return `
      <div class="oa-card" data-task="${escapeHtml(t.id)}">
        <div class="oa-row" style="justify-content:space-between;margin-bottom:4px">
          <span class="oa-pill ${status}">${escapeHtml(status)}</span>
          <span style="font-size:11px;color:var(--oa-text-muted)">${escapeHtml(fmtTime(t.created_at))}</span>
        </div>
        <div style="font-size:13px;margin:4px 0">${escapeHtml(ts.slice(0, 100))}${ts.length > 100 ? "…" : ""}</div>
        <div class="oa-row" style="justify-content:space-between">
          <span style="font-size:12px;color:var(--oa-text-muted)">${escapeHtml(subtitle) || "&nbsp;"}</span>
          ${cancelBtn}
        </div>
      </div>
    `;
  }

  class TaskPanel {
    constructor(opts) {
      this.root = typeof opts.root === "string" ? document.querySelector(opts.root) : opts.root;
      this.apiBase = (opts.apiBase || (window.OpenAkita && OpenAkita.meta.apiBase) || "").replace(/\/$/, "");
      this.tasksPath = opts.tasksPath || "/tasks";
      this.cancelPath = opts.cancelPath || "/tasks/{id}/cancel";
      this.pollInterval = opts.pollInterval || 6000;
      this.statusFilter = opts.statusFilter || "";
      this.limit = opts.limit || 30;
      this._timer = null;
      this._lastTasks = [];
      this._listeners = { update: [] };

      if (this.root) {
        this.root.addEventListener("click", (e) => {
          const btn = e.target && e.target.closest && e.target.closest("[data-cancel]");
          if (!btn) return;
          const id = btn.getAttribute("data-cancel");
          if (!id) return;
          btn.disabled = true;
          this.cancel(id).finally(() => { btn.disabled = false; });
        });
      }
    }

    on(eventName, fn) {
      (this._listeners[eventName] = this._listeners[eventName] || []).push(fn);
    }

    _emit(eventName, payload) {
      for (const fn of (this._listeners[eventName] || [])) {
        try { fn(payload); } catch (e) { /* ignore */ }
      }
    }

    async refresh() {
      try {
        const url = new URL(this.apiBase + this.tasksPath, location.origin);
        if (this.statusFilter) url.searchParams.set("status", this.statusFilter);
        url.searchParams.set("limit", String(this.limit));
        const r = await fetch(url.toString());
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const tasks = await r.json();
        this._lastTasks = Array.isArray(tasks) ? tasks : (tasks.tasks || []);
        this._render();
        this._emit("update", this._lastTasks);
        return this._lastTasks;
      } catch (e) {
        if (this.root) {
          this.root.innerHTML = `<div class="oa-card"><p>列表加载失败: ${escapeHtml(e.message || "")}，自动重试中…</p></div>`;
        }
        return [];
      }
    }

    async cancel(taskId) {
      const path = this.cancelPath.replace("{id}", encodeURIComponent(taskId));
      try {
        const r = await fetch(this.apiBase + path, { method: "POST" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await this.refresh();
        return true;
      } catch (e) {
        if (window.OpenAkita && OpenAkita.notify) {
          OpenAkita.notify({ title: "取消失败", body: e.message || "", type: "error" });
        }
        return false;
      }
    }

    start() {
      this.stop();
      this.refresh();
      this._timer = setInterval(() => this.refresh(), this.pollInterval);
    }

    stop() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
    }

    _render() {
      if (!this.root) return;
      if (!this._lastTasks.length) {
        this.root.innerHTML = `<div class="oa-card"><p>还没有任务，去上面试试看吧。</p></div>`;
        return;
      }
      this.root.innerHTML = this._lastTasks.map(renderRow).join("");
    }
  }

  window.TaskPanel = TaskPanel;
})();
