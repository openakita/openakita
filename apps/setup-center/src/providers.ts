// ─── Provider-related utility functions for Setup Center ───

import { IS_TAURI, proxyFetch } from "./platform";
import { authFetch, isTauriRemoteMode } from "./platform/auth";
import type { ProviderInfo, ListedModel } from "./types";

/** 判断服务商是否为本地服务（不需要真实 API Key） */
export function isLocalProvider(p: ProviderInfo | null | undefined): boolean {
  return p?.requires_api_key === false || p?.is_local === true;
}

/** 获取本地服务商的默认 placeholder API key */
export function localProviderPlaceholderKey(p: ProviderInfo | null | undefined): string {
  return p?.slug || "local";
}

/**
 * 将模型拉取的原始错误转换为用户友好的提示信息。
 * @param rawError 原始错误字符串
 * @param t i18n 翻译函数
 * @param providerName 服务商显示名称（可选，用于本地服务提示）
 */
export function friendlyFetchError(rawError: string, t: (k: string, vars?: Record<string, unknown>) => string, providerName?: string): string {
  const e = rawError.toLowerCase();

  if (e.includes("failed to fetch") || e.includes("networkerror") || e.includes("network error") || e.includes("error sending request") || e.includes("fetch failed")) {
    if (providerName && (e.includes("localhost") || e.includes("127.0.0.1") || e.includes("0.0.0.0"))) {
      return t("llm.fetchErrorLocalNotRunning", { provider: providerName });
    }
    return t("llm.fetchErrorNetwork");
  }
  if (e.includes("401") || e.includes("unauthorized") || e.includes("invalid api key") || e.includes("invalid_api_key") || e.includes("authentication")) {
    return t("llm.fetchErrorAuth");
  }
  if (e.includes("403") || e.includes("forbidden") || e.includes("permission")) {
    return t("llm.fetchErrorForbidden");
  }
  if (e.includes("404") || e.includes("not found")) {
    return t("llm.fetchErrorNotFound");
  }
  if (e.includes("timeout") || e.includes("aborterror") || e.includes("timed out") || e.includes("deadline")) {
    return t("llm.fetchErrorTimeout");
  }
  const detail = rawError.length > 120 ? rawError.slice(0, 120) + "…" : rawError;
  return t("llm.fetchErrorUnknown", { detail });
}

/**
 * 前端版 infer_capabilities：根据模型名推断能力。
 * 与 Python 端 openakita.llm.capabilities.infer_capabilities 的关键词规则保持一致。
 *
 * ⚠ 维护提示：如果 Python 端的推断规则有修改，需要同步更新此函数。
 * 参见: src/openakita/llm/capabilities.py → infer_capabilities()
 */
export function inferCapabilities(modelName: string, _providerSlug?: string | null): Record<string, boolean> {
  const m = modelName.toLowerCase();
  const caps: Record<string, boolean> = { text: true, vision: false, video: false, tools: false, thinking: false };

  if (["vl", "vision", "visual", "image", "-v-", "4v"].some(kw => m.includes(kw))) caps.vision = true;
  if (["kimi", "gemini"].some(kw => m.includes(kw))) caps.video = true;
  if (["thinking", "r1", "qwq", "qvq", "o1"].some(kw => m.includes(kw))) caps.thinking = true;
  if (["qwen", "gpt", "claude", "deepseek", "kimi", "glm", "gemini", "moonshot", "minimax", "doubao"].some(kw => m.includes(kw))) caps.tools = true;
  if (m.includes("minimax") && m.includes("m2")) caps.thinking = true;

  return caps;
}

export function isMiniMaxProvider(providerSlug: string | null, baseUrl: string): boolean {
  const slug = (providerSlug || "").toLowerCase();
  const base = (baseUrl || "").toLowerCase();
  return ["minimax", "minimax-cn", "minimax-int"].includes(slug) || base.includes("minimax") || base.includes("minimaxi");
}

export function isVolcCodingPlanProvider(providerSlug: string | null, baseUrl: string): boolean {
  const slug = (providerSlug || "").toLowerCase();
  const base = (baseUrl || "").toLowerCase();
  const isVolc = slug === "volcengine" || base.includes("volces.com");
  return isVolc && base.includes("/api/coding");
}

export function isLongCatProvider(providerSlug: string | null, baseUrl: string): boolean {
  const slug = (providerSlug || "").toLowerCase();
  const base = (baseUrl || "").toLowerCase();
  return slug === "longcat" || base.includes("longcat.chat");
}

export function isDashScopeCodingPlanProvider(providerSlug: string | null, baseUrl: string): boolean {
  const slug = (providerSlug || "").toLowerCase();
  const base = (baseUrl || "").toLowerCase();
  const isDash = slug === "dashscope" || slug === "dashscope-intl" || base.includes("dashscope.aliyuncs.com");
  return isDash && base.includes("coding");
}

export function isQianFanCodingPlanProvider(providerSlug: string | null, baseUrl: string): boolean {
  const slug = (providerSlug || "").toLowerCase();
  const base = (baseUrl || "").toLowerCase();
  const isQf = slug === "qianfan" || base.includes("qianfan.baidubce.com");
  return isQf && base.includes("coding");
}

export function miniMaxFallbackModels(providerSlug: string | null): ListedModel[] {
  const ids = [
    "MiniMax-M2.7",
    "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1",
    "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
  ];
  return ids.map((id) => ({
    id,
    name: id,
    capabilities: inferCapabilities(id, providerSlug),
  }));
}

export function volcCodingPlanFallbackModels(providerSlug: string | null): ListedModel[] {
  const ids = [
    "doubao-seed-2.0-code",
    "doubao-seed-code",
    "glm-4.7",
    "deepseek-v3.2",
    "kimi-k2-thinking",
    "kimi-k2.5",
  ];
  return ids.map((id) => ({
    id,
    name: id,
    capabilities: inferCapabilities(id, providerSlug),
  }));
}

export function longCatFallbackModels(providerSlug: string | null): ListedModel[] {
  const ids = [
    "LongCat-Flash-Chat",
    "LongCat-Flash-Thinking",
    "LongCat-Flash-Thinking-2601",
    "LongCat-Flash-Lite",
  ];
  return ids.map((id) => ({
    id,
    name: id,
    capabilities: inferCapabilities(id, providerSlug),
  }));
}

export function qianFanCodingPlanFallbackModels(providerSlug: string | null): ListedModel[] {
  const ids = [
    "kimi-k2.5",
    "deepseek-v3.2",
    "glm-5",
    "minimax-m2.5",
  ];
  return ids.map((id) => ({
    id,
    name: id,
    capabilities: inferCapabilities(id, providerSlug),
  }));
}

export function dashScopeCodingPlanFallbackModels(providerSlug: string | null): ListedModel[] {
  const ids = [
    "qwen3.5-plus",
    "kimi-k2.5",
    "glm-5",
    "MiniMax-M2.5",
    "qwen3-max-2026-01-23",
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "glm-4.7",
  ];
  return ids.map((id) => ({
    id,
    name: id,
    capabilities: inferCapabilities(id, providerSlug),
  }));
}

/**
 * 前端直连服务商 API 拉取模型列表。
 * 通过 Rust http_proxy_request 命令代理发送，绕过 WebView CORS 限制。
 */
export async function fetchModelsDirectly(params: {
  apiType: string; baseUrl: string; providerSlug: string | null; apiKey: string;
}): Promise<ListedModel[]> {
  const { apiType, baseUrl, providerSlug, apiKey } = params;
  const base = baseUrl.replace(/\/+$/, "");

  if (isVolcCodingPlanProvider(providerSlug, baseUrl)) {
    return volcCodingPlanFallbackModels(providerSlug);
  }
  if (isDashScopeCodingPlanProvider(providerSlug, baseUrl)) {
    return dashScopeCodingPlanFallbackModels(providerSlug);
  }
  if (isQianFanCodingPlanProvider(providerSlug, baseUrl)) {
    return qianFanCodingPlanFallbackModels(providerSlug);
  }
  if (isLongCatProvider(providerSlug, baseUrl)) {
    return longCatFallbackModels(providerSlug);
  }

  if (apiType === "anthropic") {
    if (isMiniMaxProvider(providerSlug, baseUrl)) {
      return miniMaxFallbackModels(providerSlug);
    }

    const url = base.endsWith("/v1") ? `${base}/models` : `${base}/v1/models`;
    const resp = await proxyFetch(url, {
      headers: {
        "x-api-key": apiKey,
        Authorization: `Bearer ${apiKey}`,
        "anthropic-version": "2023-06-01",
      },
      timeoutSecs: 30,
    });
    if (resp.status >= 400) {
      if (resp.status === 404 && isMiniMaxProvider(providerSlug, baseUrl)) {
        return miniMaxFallbackModels(providerSlug);
      }
      throw new Error(`Anthropic API ${resp.status}: ${resp.body.slice(0, 200)}`);
    }
    const data = JSON.parse(resp.body);
    return (data.data ?? [])
      .map((m: any) => ({
        id: String(m.id ?? "").trim(),
        name: String(m.display_name ?? m.id ?? ""),
        capabilities: inferCapabilities(String(m.id ?? ""), providerSlug),
      }))
      .filter((m: ListedModel) => m.id);
  }

  // OpenAI-compatible: GET /models
  if (isMiniMaxProvider(providerSlug, baseUrl)) {
    return miniMaxFallbackModels(providerSlug);
  }

  const url = `${base}/models`;
  const resp = await proxyFetch(url, {
    headers: { Authorization: `Bearer ${apiKey}` },
    timeoutSecs: 30,
  });
  if (resp.status >= 400) {
    if (resp.status === 404 && isMiniMaxProvider(providerSlug, baseUrl)) {
      return miniMaxFallbackModels(providerSlug);
    }
    throw new Error(`API ${resp.status}: ${resp.body.slice(0, 200)}`);
  }
  const data = JSON.parse(resp.body);
  return (data.data ?? [])
    .map((m: any) => ({
      id: String(m.id ?? "").trim(),
      name: String(m.id ?? ""),
      capabilities: inferCapabilities(String(m.id ?? ""), providerSlug),
    }))
    .filter((m: ListedModel) => m.id)
    .sort((a: ListedModel, b: ListedModel) => a.id.localeCompare(b.id));
}

/**
 * fetch wrapper: 在 HTTP 4xx/5xx 时自动抛异常（原生 fetch 只在网络错误时才抛）。
 * 所有对后端 API 的调用都应使用此函数，以确保错误被正确捕获。
 * Web 模式下自动携带 JWT token 并支持静默续期。
 */
export async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  const effectiveInit = init?.signal ? init : { ...init, signal: AbortSignal.timeout(10_000) };
  const useAuth = !IS_TAURI || isTauriRemoteMode();
  let apiBase = "";
  if (useAuth && url.startsWith("http")) {
    try { apiBase = new URL(url).origin; } catch { /* relative url, keep "" */ }
    if (!apiBase || apiBase === "null" || apiBase === window.location.origin) apiBase = "";
  }
  const res = useAuth
    ? await authFetch(url, effectiveInit, apiBase)
    : await fetch(url, effectiveInit);
  if (!res.ok) {
    let userMessage = res.statusText;
    try {
      const body = await res.text();
      if (body) {
        try {
          const parsed = JSON.parse(body);
          const errObj = parsed?.detail?.error ?? parsed?.detail;
          if (typeof errObj === "object" && errObj?.message) {
            userMessage = errObj.message;
            if (errObj.guidance) userMessage += `\n${errObj.guidance}`;
          } else if (typeof parsed?.detail === "string") {
            userMessage = parsed.detail;
          } else {
            userMessage = body.slice(0, 200);
          }
        } catch {
          userMessage = body.slice(0, 200);
        }
      }
    } catch { /* ignore */ }
    throw new Error(userMessage);
  }
  return res;
}

// Re-export proxyFetch from platform layer for backward compatibility.
// Tauri: proxied through Rust to bypass WebView CORS.
// Web: direct fetch (same-origin, no CORS issue).
export { proxyFetch } from "./platform";
