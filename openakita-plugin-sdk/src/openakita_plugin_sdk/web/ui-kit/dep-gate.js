/**
 * OpenAkita Plugin UI Kit — dep-gate.js
 *
 * Declarative system-dependency banner.  Drop this script into a plugin and
 * mark any container (or empty <div>) with:
 *
 *   <div data-oa-dep="ffmpeg"></div>
 *   <div data-oa-dep="ffmpeg,whisper.cpp"></div>
 *
 * On DOMContentLoaded the script:
 *   1. Calls /api/plugins/_sdk/deps/check to see what is missing.
 *   2. For each missing dep, renders a banner with:
 *        - icon + display name + description
 *        - "一键安装" (one-click install) button (when an automated method exists)
 *        - "手动安装" link to homepage / manual_url (fallback)
 *        - expandable log panel
 *   3. Clicking 一键安装 opens a confirmation modal showing the exact argv,
 *      then opens an SSE stream and renders progress live.
 *   4. On the "done" event the banner is replaced with a green "已就绪" pill
 *      and the document dispatches `openakita:dep-ready` with the dep id —
 *      plugin code can listen for this to re-enable disabled UI sections.
 *
 * No external deps.  Uses the same CSS variables as the rest of the UI Kit
 * (.oa-dep-banner styles live in styles.css).
 *
 * Public API (window.OpenAkitaDepGate):
 *   init(root?)        – scan for [data-oa-dep] under root; auto-called on
 *                         DOMContentLoaded so plugins rarely need this.
 *   refresh(depId?)    – re-check a single dep (or all) and re-render.
 *   isReady(depId)     – sync boolean from cached check result.
 *
 * Public events (dispatched on document):
 *   openakita:dep-ready     – { depId }  emitted on first detection success
 *                              and after a successful install.
 *   openakita:dep-missing   – { depId, dep }  emitted when a banner is shown.
 *   openakita:dep-error     – { depId, error }  emitted on installer failure.
 */
(function () {
  if (typeof window === "undefined") return;

  const API = "/api/plugins/_sdk/deps";

  // Built-in i18n strings.  Plugins that override these keys via
  // OpenAkitaI18n.register({...}) win automatically — `t()` checks the
  // active dictionary first before falling back to the default below.
  const DEP_GATE_DICT = {
    zh: {
      "oa.dep.checking": "检测中…",
      "oa.dep.checking_env": "正在检测系统组件…",
      "oa.dep.ready": "已就绪",
      "oa.dep.missing": "缺失",
      "oa.dep.install_one_click": "一键安装",
      "oa.dep.manual_install": "手动安装",
      "oa.dep.no_method": "暂无自动安装方案",
      "oa.dep.requires_root": "需要管理员权限",
      "oa.dep.sudo": "sudo",
      "oa.dep.confirm_install_title": "即将安装",
      "oa.dep.confirm_install_desc": "命令：",
      "oa.dep.confirm_install_time": "预计耗时：",
      "oa.dep.confirm_install_continue": "继续吗？",
      "oa.dep.install_failed_prefix": "安装失败：",
      "oa.dep.catalog_failed": "无法加载依赖目录：",
      "oa.dep.check_failed": "检测失败：",
    },
    en: {
      "oa.dep.checking": "Checking…",
      "oa.dep.checking_env": "Checking system components…",
      "oa.dep.ready": "Ready",
      "oa.dep.missing": "Missing",
      "oa.dep.install_one_click": "Install with one click",
      "oa.dep.manual_install": "Install manually",
      "oa.dep.no_method": "No automated installer",
      "oa.dep.requires_root": "Requires administrator rights",
      "oa.dep.sudo": "sudo",
      "oa.dep.confirm_install_title": "About to install",
      "oa.dep.confirm_install_desc": "Command:",
      "oa.dep.confirm_install_time": "Estimated time:",
      "oa.dep.confirm_install_continue": "Continue?",
      "oa.dep.install_failed_prefix": "Install failed: ",
      "oa.dep.catalog_failed": "Failed to load dependency catalog: ",
      "oa.dep.check_failed": "Detection failed: ",
    },
  };

  function ensureDict() {
    if (window.OpenAkitaI18n && typeof window.OpenAkitaI18n.register === "function") {
      try { window.OpenAkitaI18n.register(DEP_GATE_DICT); } catch (e) { /* ignore */ }
    }
  }
  ensureDict();
  const KNOWN_STATUS = new Map(); // depId -> { found, version, location, dep }
  const BANNERS = new Map();      // depId -> Element

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function dispatch(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function t(key, fallback) {
    if (window.OpenAkitaI18n && typeof window.OpenAkitaI18n.t === "function") {
      const v = window.OpenAkitaI18n.t(key);
      if (v && v !== key) return v;
    }
    return fallback;
  }

  async function fetchJson(url, opts) {
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status}: ${text || resp.statusText}`);
    }
    return resp.json();
  }

  function statusPill(status) {
    if (!status) return `<span class="oa-pill pending">${escapeHtml(t("oa.dep.checking", "检测中…"))}</span>`;
    if (status.found) {
      const ver = status.version ? ` v${escapeHtml(status.version)}` : "";
      return `<span class="oa-pill succeeded">${escapeHtml(t("oa.dep.ready", "已就绪"))}${ver}</span>`;
    }
    return `<span class="oa-pill failed">${escapeHtml(t("oa.dep.missing", "缺失"))}</span>`;
  }

  function bannerHtml(dep, status, methods) {
    const methodList = methods || [];
    const auto = methodList.find((m) => m.strategy !== "manual");
    const manualMethod = methodList.find((m) => m.strategy === "manual") || {};
    const manualUrl = manualMethod.manual_url || dep.homepage || "";

    let actions = "";
    if (auto) {
      const sudoBadge = auto.requires_sudo
        ? `<span class="oa-dep-banner__sudo" title="${escapeHtml(t("oa.dep.requires_root", "需要管理员权限"))}">${escapeHtml(t("oa.dep.sudo", "sudo"))}</span>`
        : "";
      actions += `
        <button class="oa-btn oa-btn-primary oa-dep-banner__install" data-strategy="${escapeHtml(auto.strategy)}">
          ${escapeHtml(t("oa.dep.install_one_click", "一键安装"))}
          <span class="oa-dep-banner__strategy">${escapeHtml(auto.strategy)}</span>
          ${sudoBadge}
        </button>`;
    }
    if (manualUrl) {
      actions += `<a class="oa-btn oa-dep-banner__manual" href="${escapeHtml(manualUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("oa.dep.manual_install", "手动安装"))}</a>`;
    }
    if (!actions) {
      actions = `<span class="oa-dep-banner__no-method">${escapeHtml(t("oa.dep.no_method", "暂无自动安装方案"))}</span>`;
    }

    return `
      <div class="oa-dep-banner" data-dep-id="${escapeHtml(dep.id)}">
        <div class="oa-dep-banner__row">
          <div class="oa-dep-banner__head">
            <strong class="oa-dep-banner__title">${escapeHtml(dep.display_name || dep.id)}</strong>
            ${statusPill(status)}
          </div>
          <div class="oa-dep-banner__actions">${actions}</div>
        </div>
        <div class="oa-dep-banner__desc">${escapeHtml(dep.description || "")}</div>
        <div class="oa-dep-banner__log" hidden></div>
      </div>
    `;
  }

  function readyBannerHtml(dep, status) {
    const ver = status.version ? ` <span class="oa-dep-banner__ver">v${escapeHtml(status.version)}</span>` : "";
    return `
      <div class="oa-dep-banner oa-dep-banner--ready" data-dep-id="${escapeHtml(dep.id)}">
        <div class="oa-dep-banner__row">
          <div class="oa-dep-banner__head">
            <strong class="oa-dep-banner__title">${escapeHtml(dep.display_name || dep.id)}</strong>
            ${statusPill(status)}${ver}
          </div>
        </div>
      </div>
    `;
  }

  async function loadCatalogIndex() {
    if (loadCatalogIndex._cache) return loadCatalogIndex._cache;
    const data = await fetchJson(`${API}/catalog`);
    const byId = {};
    for (const item of data.items || []) byId[item.id] = item;
    loadCatalogIndex._cache = { platform: data.platform, byId };
    return loadCatalogIndex._cache;
  }

  function methodsForPlatform(dep, platform) {
    return (dep.install_methods || []).filter((m) => m.platform === platform);
  }

  async function renderTo(container, depIds) {
    container.classList.add("oa-dep-banner-host");
    container.innerHTML = `<div class="oa-dep-banner oa-dep-banner--loading"><div class="oa-thinking"><span class="dot"></span><span class="dot"></span><span class="dot"></span> ${escapeHtml(t("oa.dep.checking_env", "正在检测系统组件…"))}</div></div>`;

    let catalog;
    try {
      catalog = await loadCatalogIndex();
    } catch (err) {
      container.innerHTML = `<div class="oa-dep-banner oa-dep-banner--error">${escapeHtml(t("oa.dep.catalog_failed", "无法加载依赖目录："))}${escapeHtml(err.message || String(err))}</div>`;
      return;
    }

    const validIds = depIds.filter((id) => catalog.byId[id]);
    if (validIds.length === 0) {
      container.innerHTML = "";
      return;
    }

    let statuses;
    try {
      const j = await fetchJson(`${API}/check?ids=${encodeURIComponent(validIds.join(","))}&force=1`);
      statuses = j.statuses || {};
    } catch (err) {
      container.innerHTML = `<div class="oa-dep-banner oa-dep-banner--error">${escapeHtml(t("oa.dep.check_failed", "检测失败："))}${escapeHtml(err.message || String(err))}</div>`;
      return;
    }

    const html = [];
    for (const id of validIds) {
      const dep = catalog.byId[id];
      const status = statuses[id] || { found: false };
      KNOWN_STATUS.set(id, { ...status, dep });
      if (status.found) {
        html.push(readyBannerHtml(dep, status));
        dispatch("openakita:dep-ready", { depId: id });
      } else {
        html.push(bannerHtml(dep, status, methodsForPlatform(dep, catalog.platform)));
        dispatch("openakita:dep-missing", { depId: id, dep });
      }
    }
    container.innerHTML = html.join("");
    BANNERS.set(container, validIds);

    container.querySelectorAll(".oa-dep-banner__install").forEach((btn) => {
      btn.addEventListener("click", () => {
        const banner = btn.closest(".oa-dep-banner");
        if (banner) startInstall(container, banner, catalog);
      });
    });
  }

  function confirm(message) {
    return new Promise((resolve) => {
      const ok = window.confirm(message);
      resolve(!!ok);
    });
  }

  async function startInstall(container, banner, catalog) {
    const depId = banner.getAttribute("data-dep-id");
    const dep = catalog.byId[depId];
    if (!dep) return;
    const platform = catalog.platform;
    const methods = methodsForPlatform(dep, platform);
    const auto = methods.find((m) => m.strategy !== "manual");
    if (!auto) return;

    const cmdHint = (auto.requires_sudo ? "sudo " : "") + auto.strategy;
    const proceed = await confirm(
      `${t("oa.dep.confirm_install_title", "即将安装")} ${dep.display_name}\n\n` +
      `${t("oa.dep.confirm_install_desc", "命令：")} ${cmdHint}\n` +
      `${t("oa.dep.confirm_install_time", "预计耗时：")} ~${auto.estimated_seconds}s\n\n` +
      `${t("oa.dep.confirm_install_continue", "继续吗？")}`
    );
    if (!proceed) return;

    const log = banner.querySelector(".oa-dep-banner__log");
    const installBtn = banner.querySelector(".oa-dep-banner__install");
    if (installBtn) installBtn.setAttribute("disabled", "true");
    log.hidden = false;
    log.innerHTML = "";

    function appendLog(text, cls) {
      const line = document.createElement("div");
      line.className = `oa-dep-banner__log-line${cls ? " " + cls : ""}`;
      line.textContent = text;
      log.appendChild(line);
      log.scrollTop = log.scrollHeight;
    }

    let installerError = null;
    try {
      const resp = await fetch(`${API}/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: depId, method_index: methods.indexOf(auto) }),
      });
      if (!resp.ok || !resp.body) {
        const text = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status}: ${text || resp.statusText}`);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const chunks = buf.split("\n\n");
        buf = chunks.pop() || "";
        for (const chunk of chunks) {
          const line = chunk.replace(/^data:\s*/, "").trim();
          if (!line) continue;
          let payload;
          try { payload = JSON.parse(line); } catch { continue; }
          handleEvent(payload, appendLog, () => installerError = payload);
        }
      }
    } catch (err) {
      appendLog(`${t("oa.dep.install_failed_prefix", "安装失败：")} ${err.message || err}`, "oa-dep-banner__log-line--err");
      installerError = { phase: "error", line: String(err) };
    }

    if (installerError) {
      if (installBtn) installBtn.removeAttribute("disabled");
      dispatch("openakita:dep-error", { depId, error: installerError });
    } else {
      dispatch("openakita:dep-ready", { depId });
      const newStatus = KNOWN_STATUS.get(depId) || { found: true };
      banner.outerHTML = readyBannerHtml(dep, newStatus);
    }
  }

  function handleEvent(payload, appendLog, recordError) {
    const cls = payload.phase === "stderr"
      ? "oa-dep-banner__log-line--err"
      : payload.phase === "error"
      ? "oa-dep-banner__log-line--err"
      : payload.phase === "done"
      ? "oa-dep-banner__log-line--ok"
      : "";
    if (payload.line) appendLog(payload.line, cls);
    if (payload.phase === "exit" && typeof payload.return_code === "number") {
      appendLog(`exit ${payload.return_code}`, payload.return_code === 0 ? "oa-dep-banner__log-line--ok" : "oa-dep-banner__log-line--err");
    }
    if (payload.phase === "done") {
      const ext = payload.extra || {};
      KNOWN_STATUS.set(payload.dep_id, {
        found: true,
        version: ext.version || "",
        location: ext.location || "",
        dep: KNOWN_STATUS.get(payload.dep_id)?.dep,
      });
    }
    if (payload.phase === "error" || payload.phase === "skip") {
      recordError();
    }
  }

  function init(root) {
    const scope = root || document;
    const hosts = scope.querySelectorAll("[data-oa-dep]");
    hosts.forEach((host) => {
      if (host.dataset.oaDepInit === "1") return;
      host.dataset.oaDepInit = "1";
      const ids = String(host.getAttribute("data-oa-dep") || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (ids.length === 0) return;
      renderTo(host, ids);
    });
  }

  async function refresh(depId) {
    loadCatalogIndex._cache = null;
    BANNERS.forEach((ids, container) => {
      if (!depId || ids.includes(depId)) {
        container.dataset.oaDepInit = "";
        renderTo(container, ids);
      }
    });
  }

  function isReady(depId) {
    const s = KNOWN_STATUS.get(depId);
    return !!(s && s.found);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init());
  } else {
    init();
  }

  window.OpenAkitaDepGate = { init, refresh, isReady };
})();
