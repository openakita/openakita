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

let _fetchIdCounter = 0;

/** Generate an id unique within this WebView session.
 *
 *  Used to address an in-flight `backend_fetch` so the matching Rust task
 *  can be cancelled (see `backend_fetch_cancel`). `crypto.randomUUID` is
 *  available in WebView2 / WKWebView, but the older WKWebView on macOS 11
 *  doesn't expose it on `window.crypto`; the counter fallback is fine
 *  because uniqueness only matters within one renderer process. */
function makeFetchId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    /* fall through */
  }
  _fetchIdCounter += 1;
  return `${Date.now()}-${_fetchIdCounter}`;
}

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

    // Each backend_fetch gets a unique id so the Rust task can be
    // cancelled (drops the reqwest::Response → closes TCP → stops the
    // chunk loop) when the consumer aborts. Without this, the Rust task
    // kept reading from the backend until natural EOF, queueing chunks
    // into IPC that nobody reads — burning RAM and CPU especially during
    // long LLM streams that the user already closed.
    const fetchId = makeFetchId();
    const makeAbortError = () =>
      new DOMException(
        signal?.reason?.message || "The operation was aborted",
        "AbortError",
      );
    let cancelSent = false;
    const sendCancel = () => {
      if (cancelSent) return;
      cancelSent = true;
      invoke("backend_fetch_cancel", { fetchId }).catch(() => {
        /* tab unloading / Rust already cleaned up — silent */
      });
    };
    let bodyAbortAttached = false;
    let bodyAbortHandler: (() => void) | null = null;
    const cleanupBodyAbort = () => {
      if (signal && bodyAbortAttached && bodyAbortHandler) {
        signal.removeEventListener("abort", bodyAbortHandler);
        bodyAbortAttached = false;
      }
    };

    // Channel → ReadableStream bridge: chunks arrive from Rust via IPC,
    // are enqueued into a ReadableStream that the Response body wraps.
    const channel = new Channel<FetchStreamEvent>();
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
      // Fires when a consumer calls .cancel() on the stream OR the
      // surrounding Response body is GC'd while still open. Crucially,
      // `response.body.getReader().cancel()` from useStream / SSE parser
      // routes through here, giving us a reliable hook to tell Rust to
      // stop reading.
      cancel() {
        sendCancel();
        cleanupBodyAbort();
      },
    });

    channel.onmessage = (msg: FetchStreamEvent) => {
      try {
        if (msg.event === "chunk") {
          streamController.enqueue(encoder.encode(msg.data.text));
        } else if (msg.event === "done") {
          cleanupBodyAbort();
          streamController.close();
        } else if (msg.event === "error") {
          cleanupBodyAbort();
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
        fetchId,
        url,
        method,
        headers,
        body,
      },
    );

    // Abort handling has two distinct concerns:
    //
    // (a) `abortPromise` races against `doInvoke` so that `fetch()`
    //     rejects with AbortError *synchronously* on abort, matching
    //     standard Fetch API behaviour. This listener is one-shot and
    //     removed via `cleanupAbort` once the fetch settles, otherwise
    //     it would dangle holding a closure over `streamController`
    //     and prevent the AbortController from being GC'd.
    //
    // (b) Body-lifetime listener that calls `sendCancel` whenever the
    //     signal aborts. *Critical* for LLM streaming: the HTTP headers
    //     come back in tens of ms, so `fetch()` resolves long before
    //     the body has finished streaming. If the user hits "stop"
    //     mid-body, the (a) listener is already gone — only this
    //     persistent listener can still tell Rust to stop forwarding
    //     chunks. `sendCancel` is idempotent (`cancelSent` flag) so
    //     repeat invocations are safe. We also error the body stream
    //     here; otherwise Rust may stop forwarding chunks without
    //     sending a final done/error event and `reader.read()` would
    //     hang forever.
    if (signal) {
      bodyAbortHandler = () => {
        sendCancel();
        cleanupBodyAbort();
        try {
          streamController.error(makeAbortError());
        } catch {
          /* already closed */
        }
      };
      signal.addEventListener("abort", bodyAbortHandler);
      bodyAbortAttached = true;
    }

    let onAbort: (() => void) | null = null;
    const abortPromise = signal
      ? new Promise<never>((_resolve, reject) => {
          onAbort = () => {
            // sendCancel also runs from the body-lifetime listener; the
            // duplicate call is a no-op thanks to `cancelSent`.
            sendCancel();
            try {
              streamController.error(makeAbortError());
            } catch {
              /* already closed */
            }
            reject(makeAbortError());
          };
          signal.addEventListener("abort", onAbort, { once: true });
        })
      : null;

    const cleanupAbort = () => {
      if (signal && onAbort) signal.removeEventListener("abort", onAbort);
    };

    try {
      const meta = abortPromise
        ? await Promise.race([doInvoke, abortPromise])
        : await doInvoke;
      cleanupAbort();
      return new Response(stream, {
        status: meta.status,
        headers: meta.headers,
      });
    } catch (err) {
      cleanupAbort();
      cleanupBodyAbort();
      // Tell Rust to stop too — covers the case where invoke itself
      // rejects (e.g. AbortError, IPC error) before backend_fetch
      // finished setup. sendCancel is idempotent so calling it again
      // after onAbort already did is fine.
      sendCancel();
      try {
        streamController.error(err);
      } catch {
        /* already closed */
      }
      throw err;
    }
  };
}

