/**
 * Local backend fetch override — proxy-safe transport for Tauri desktop.
 *
 * On macOS, proxy software (Clash / V2Ray) sets a system-level HTTP proxy via
 * Network Preferences.  WKWebView's native fetch() honours that proxy, causing
 * requests to 127.0.0.1 to be routed through the external proxy server — which
 * cannot reach the user's localhost backend.  The previous approach of routing
 * through @tauri-apps/plugin-http suffered the same problem because its internal
 * reqwest client reads the macOS system proxy via hyper-util/system-configuration,
 * and NO_PROXY env var does not reliably override it.
 *
 * Fix: intercept localhost fetch() calls and route them through a dedicated Tauri
 * IPC command (`backend_fetch`) whose reqwest client uses `.no_proxy()` — a hard
 * switch that completely disables ALL proxy detection.  The response body is
 * streamed back via Tauri Channel → ReadableStream, preserving SSE behaviour for
 * the chat view.
 *
 * Only localhost requests are intercepted; everything else uses native fetch.
 * In non-Tauri environments (e.g. `npm run dev` in a browser) no interception
 * is performed.
 */

const LOCAL_RE = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?(?:\/|$)/;

type FetchStreamEvent =
  | { event: "chunk"; data: { text: string } }
  | { event: "done" }
  | { event: "error"; data: { message: string } };

export function installLocalFetchOverride(): void {
  if (
    typeof window === "undefined" ||
    !("__TAURI_INTERNALS__" in window)
  ) {
    return;
  }

  const nativeFetch = window.fetch.bind(window);

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

    // Non-string bodies (FormData, Blob, etc.) can't be serialised through IPC.
    // Fall back to native fetch for these rare cases (e.g. feedback file upload).
    if (init?.body && typeof init.body !== "string") {
      return nativeFetch(input, init);
    }

    const { invoke, Channel } = await import("@tauri-apps/api/core");

    const method = init?.method ?? "GET";
    const headers: Record<string, string> = {};
    if (init?.headers) {
      const h =
        init.headers instanceof Headers
          ? init.headers
          : new Headers(init.headers as HeadersInit);
      h.forEach((v, k) => {
        headers[k] = v;
      });
    }
    const body = typeof init?.body === "string" ? init.body : null;
    const signal = init?.signal;

    if (signal?.aborted) {
      throw new DOMException(
        signal.reason?.message || "The operation was aborted",
        "AbortError",
      );
    }

    // Channel → ReadableStream bridge: chunks arrive from Rust via IPC,
    // are enqueued into a ReadableStream that the Response body wraps.
    const channel = new Channel<FetchStreamEvent>();
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });

    channel.onmessage = (msg: FetchStreamEvent) => {
      try {
        if (msg.event === "chunk") {
          streamController.enqueue(encoder.encode(msg.data.text));
        } else if (msg.event === "done") {
          streamController.close();
        } else if (msg.event === "error") {
          streamController.error(new Error(msg.data.message));
        }
      } catch {
        // Stream already closed/errored — ignore
      }
    };

    const doInvoke = invoke<{ status: number; headers: Record<string, string> }>(
      "backend_fetch",
      {
        onEvent: channel,
        url,
        method,
        headers,
        body,
      },
    );

    // Handle AbortSignal: race the invoke against the abort event.
    // If aborted, the Rust background task will eventually stop when it
    // detects the channel is closed.
    const metaPromise = signal
      ? Promise.race([
          doInvoke,
          new Promise<never>((_resolve, reject) => {
            const onAbort = () =>
              reject(
                new DOMException(
                  signal.reason?.message || "The operation was aborted",
                  "AbortError",
                ),
              );
            signal.addEventListener("abort", onAbort, { once: true });
            doInvoke
              .then(() => signal.removeEventListener("abort", onAbort))
              .catch(() => signal.removeEventListener("abort", onAbort));
          }),
        ])
      : doInvoke;

    try {
      const meta = await metaPromise;
      return new Response(stream, {
        status: meta.status,
        headers: meta.headers,
      });
    } catch (err) {
      // Close the stream so readers don't hang
      try {
        streamController.error(err);
      } catch {
        /* already closed */
      }
      throw err;
    }
  };
}

