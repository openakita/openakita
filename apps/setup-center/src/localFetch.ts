/**
 * 全局 fetch 拦截：代理软件运行时，macOS WKWebView 的 fetch() 遵守系统代理设置，
 * 导致对 127.0.0.1 的请求被路由到代理服务器而非直连本地后端。
 *
 * 解决方案：用 Tauri 官方 HTTP 插件的 fetch() 替代 WebView 原生 fetch()。
 * 插件走 Rust reqwest，配合 Rust 端 NO_PROXY 环境变量绕过系统代理。
 * 支持 JSON、FormData、SSE 流式响应，与原生 fetch 行为一致。
 *
 * 非 localhost 请求不受影响，仍走浏览器原生 fetch。
 *
 * 降级策略：仅在插件不可用（如 dev server 纯浏览器环境）时降级到原生 fetch，
 * 且只降级一次（记录标记避免反复尝试）。运行时错误（网络超时、代理阻断等）
 * 不降级，直接抛出让调用方处理。
 */
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

const LOCAL_RE = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?(?:\/|$)/;

export function installLocalFetchOverride(): void {
  const nativeFetch = window.fetch.bind(window);
  let pluginUnavailable = false;

  window.fetch = async function (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    let url: string;
    if (typeof input === "string") url = input;
    else if (input instanceof URL) url = input.toString();
    else if (input instanceof Request) url = input.url;
    else return nativeFetch(input, init);

    if (!LOCAL_RE.test(url)) {
      return nativeFetch(input, init);
    }

    if (pluginUnavailable) {
      return nativeFetch(input, init);
    }

    try {
      return await tauriFetch(input, init);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      if (init?.signal?.aborted) {
        throw new DOMException("The operation was aborted.", "AbortError");
      }
      // Plugin init/scope errors (e.g. running outside Tauri) → fall back permanently.
      // Runtime HTTP errors (connection refused, timeout, proxy block) → propagate.
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("not found") || msg.includes("plugin") || msg.includes("not initialized")) {
        console.warn("[localFetch] Tauri HTTP plugin unavailable, falling back to native fetch:", msg);
        pluginUnavailable = true;
        return nativeFetch(input, init);
      }
      throw e;
    }
  };
}
