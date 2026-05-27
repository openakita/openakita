/**
 * V2 SSE stream client.
 *
 * Default URL is the Sprint-9 alias ``GET
 * /api/v2/orgs/{id}/events/stream`` (set by
 * ``api/routes/orgs_v2_runtime_dispatch.py``); the legacy
 * ``/api/v2/orgs-spec/{id}/stream`` path is still served by
 * ``api/routes/orgs_v2_stream.py`` for backward-compat and can be
 * targeted by passing ``apiPath`` to ``createV2Stream``.
 *
 * Wraps the browser ``EventSource`` API with a typed handler
 * registry so callers do not have to remember channel-name
 * strings. Sprint-9 exposes six channels:
 *   - ``progress_ledger`` -- per-turn supervisor ledger snapshots
 *   - ``messages``        -- node-to-node messenger traffic
 *   - ``lifecycle``       -- supervisor start / done / cancelled /
 *                            resumed events
 *   - ``tasks``           -- task ledger updates (facts / plan)
 *   - ``stalls``          -- StallDetector signals (warn / replan)
 *   - ``replans``         -- replan-budget moves
 *
 * Returned object:
 *   onEvent(channel, handler) -> unsubscribe function
 *   onError(handler)          -> unsubscribe function
 *   close()                   -> tears down the EventSource
 *
 * Example:
 *   const stream = createV2Stream("org_123");
 *   const off = stream.onEvent("progress_ledger", (e) => {
 *     console.log(e.payload.next_speaker);
 *   });
 *   // later: off(); stream.close();
 *
 * The factory accepts an ``eventSourceFactory`` option so unit
 * tests can inject a mock without monkey-patching the global.
 */

export type V2StreamChannel =
  | "progress_ledger"
  | "messages"
  | "lifecycle"
  | "tasks"
  | "stalls"
  | "replans";

/**
 * Shape of one event delivered through the SSE stream.
 *
 * Backend ``_serialize_event`` (in ``api/routes/orgs_v2_stream.py``)
 * drops the ``channel`` field from the JSON ``data`` payload --
 * channel is on the SSE ``event:`` line -- and adds a ``ts`` mirror
 * of ``emitted_at``. Everything else is what
 * ``StreamEvent.to_jsonable()`` produces.
 */
export interface V2StreamEvent {
  type: string;
  payload: Record<string, unknown>;
  org_id: string;
  command_id: string;
  superstep: number;
  ts: string;
  emitted_at?: string;
  event_id?: string;
  correlation_id?: string | null;
}

export type V2EventHandler = (event: V2StreamEvent) => void;
export type V2ErrorHandler = (event: Event) => void;
export type V2Unsubscribe = () => void;

export interface V2Stream {
  onEvent(channel: V2StreamChannel, handler: V2EventHandler): V2Unsubscribe;
  onError(handler: V2ErrorHandler): V2Unsubscribe;
  close(): void;
  readonly url: string;
  readonly readyState: number;
}

/** Subset of EventSource we depend on (lets tests mock it). */
export interface EventSourceLike {
  readyState: number;
  url: string;
  addEventListener(type: string, listener: (ev: MessageEvent | Event) => void): void;
  removeEventListener(type: string, listener: (ev: MessageEvent | Event) => void): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

export interface V2StreamOptions {
  /** Override the URL prefix (default: same-origin "/"). */
  apiBase?: string;
  /**
   * Override the per-org path template. Use ``"{id}"`` as the
   * placeholder. Defaults to the Sprint-9 alias
   * ``/api/v2/orgs/{id}/events/stream``. Set to
   * ``/api/v2/orgs-spec/{id}/stream`` to talk to the legacy route.
   */
  apiPath?: string;
  /** Inject an EventSource factory (test seam; defaults to ``new EventSource``). */
  eventSourceFactory?: EventSourceFactory;
}

const DEFAULT_CHANNELS: V2StreamChannel[] = [
  "progress_ledger",
  "messages",
  "lifecycle",
  "tasks",
  "stalls",
  "replans",
];

function defaultFactory(url: string): EventSourceLike {
  // ``EventSource`` is widely supported (>97% browsers); no
  // polyfill needed. ``withCredentials`` would be required only
  // for cross-origin auth, which the v2 surface does not use.
  return new EventSource(url) as unknown as EventSourceLike;
}

/**
 * Open a typed v2 SSE stream for the given org.
 *
 * @param orgId The v2 org id (must already be persisted).
 * @param opts  Optional ``apiBase`` and ``eventSourceFactory``.
 */
export function createV2Stream(
  orgId: string,
  opts: V2StreamOptions = {},
): V2Stream {
  if (!orgId) {
    throw new Error("createV2Stream: orgId must be a non-empty string");
  }
  const apiBase = (opts.apiBase ?? "").replace(/\/+$/, "");
  const pathTemplate = opts.apiPath ?? "/api/v2/orgs/{id}/events/stream";
  const url = `${apiBase}${pathTemplate.replace("{id}", encodeURIComponent(orgId))}`;
  const factory = opts.eventSourceFactory ?? defaultFactory;
  const source = factory(url);

  const handlers: Map<V2StreamChannel, Set<V2EventHandler>> = new Map();
  const errorHandlers: Set<V2ErrorHandler> = new Set();
  const listenerRefs: Map<V2StreamChannel, (ev: MessageEvent | Event) => void> =
    new Map();

  function dispatch(channel: V2StreamChannel, ev: MessageEvent | Event): void {
    const set = handlers.get(channel);
    if (!set || set.size === 0) return;
    let parsed: V2StreamEvent;
    try {
      const data = (ev as MessageEvent).data;
      parsed = typeof data === "string" ? (JSON.parse(data) as V2StreamEvent) : data;
    } catch (err) {
      // Bad payload -- emit through error channel; do not invoke
      // typed handlers with malformed data.
      errorHandlers.forEach((h) => h(ev));
      return;
    }
    set.forEach((h) => {
      try {
        h(parsed);
      } catch (err) {
        // Handler errors must not break the stream.
        errorHandlers.forEach((eh) => eh(ev));
      }
    });
  }

  // Pre-attach EventSource listeners for every channel so the first
  // ``onEvent`` call in app code does not race the connection.
  for (const channel of DEFAULT_CHANNELS) {
    const listener = (ev: MessageEvent | Event) => dispatch(channel, ev);
    listenerRefs.set(channel, listener);
    source.addEventListener(channel, listener);
  }

  const onError = (ev: Event) => {
    errorHandlers.forEach((h) => h(ev));
  };
  source.addEventListener("error", onError);

  return {
    get url() {
      return source.url;
    },
    get readyState() {
      return source.readyState;
    },
    onEvent(channel: V2StreamChannel, handler: V2EventHandler): V2Unsubscribe {
      let set = handlers.get(channel);
      if (!set) {
        set = new Set();
        handlers.set(channel, set);
      }
      set.add(handler);
      return () => {
        set!.delete(handler);
      };
    },
    onError(handler: V2ErrorHandler): V2Unsubscribe {
      errorHandlers.add(handler);
      return () => {
        errorHandlers.delete(handler);
      };
    },
    close(): void {
      for (const [channel, listener] of listenerRefs.entries()) {
        source.removeEventListener(channel, listener);
      }
      source.removeEventListener("error", onError);
      listenerRefs.clear();
      handlers.clear();
      errorHandlers.clear();
      source.close();
    },
  };
}
