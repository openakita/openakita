import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createV2Stream,
  type EventSourceLike,
  type V2StreamEvent,
} from "../v2Stream";

/**
 * Mock EventSource the tests inject via ``eventSourceFactory``.
 *
 * Records every ``addEventListener`` registration so tests can
 * synchronously fire events through ``emit(channel, payload)``.
 */
class FakeEventSource implements EventSourceLike {
  readyState = 1;
  url: string;
  closed = false;
  listeners: Map<string, Set<(ev: MessageEvent | Event) => void>> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, l: (ev: MessageEvent | Event) => void): void {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(l);
  }

  removeEventListener(type: string, l: (ev: MessageEvent | Event) => void): void {
    this.listeners.get(type)?.delete(l);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  emit(channel: string, data: V2StreamEvent | string): void {
    const ev = new MessageEvent(channel, {
      data: typeof data === "string" ? data : JSON.stringify(data),
    });
    this.listeners.get(channel)?.forEach((l) => l(ev));
  }

  emitError(): void {
    this.listeners.get("error")?.forEach((l) => l(new Event("error")));
  }
}

let factorySource: FakeEventSource | null = null;
function fakeFactory(url: string): EventSourceLike {
  factorySource = new FakeEventSource(url);
  return factorySource;
}

afterEach(() => {
  factorySource = null;
});

describe("createV2Stream", () => {
  it("constructs the canonical URL with the org id encoded", () => {
    const stream = createV2Stream("org abc/?", { eventSourceFactory: fakeFactory });
    expect(factorySource).not.toBeNull();
    expect(factorySource!.url).toBe(
      "/api/v2/orgs/org%20abc%2F%3F/events/stream",
    );
    stream.close();
  });

  it("supports the legacy /orgs-spec stream URL via apiPath override", () => {
    const stream = createV2Stream("org_legacy", {
      eventSourceFactory: fakeFactory,
      apiPath: "/api/v2/orgs-spec/{id}/stream",
    });
    expect(factorySource!.url).toBe(
      "/api/v2/orgs-spec/org_legacy/stream",
    );
    stream.close();
  });

  it(
    "delivers supervisor stall_warning / replanning events through the lifecycle channel",
    () => {
      // v25 RC-3 contract: the supervisor publishes
      // ``stall_warning`` and ``replanning`` events on the
      // ``lifecycle`` channel (supervisor.py:448 / :514). Callers
      // listen on ``lifecycle`` and switch on ``event.type`` --
      // the obsolete standalone ``stalls`` / ``replans`` channels
      // were removed from V2StreamChannel because nothing ever
      // emitted to them. See v2Stream.ts channel-list comment.
      const lifecycle = vi.fn();
      const stream = createV2Stream("org_s9", {
        eventSourceFactory: fakeFactory,
      });
      stream.onEvent("lifecycle", lifecycle);

      factorySource!.emit("lifecycle", {
        type: "stall_warning",
        payload: { n_stalls: 1, max_stalls: 3 },
        org_id: "org_s9",
        command_id: "c",
        superstep: 2,
        ts: "t",
      });
      factorySource!.emit("lifecycle", {
        type: "replanning",
        payload: { reason: "stall_budget_reached", n_replans: 1 },
        org_id: "org_s9",
        command_id: "c",
        superstep: 3,
        ts: "t",
      });

      expect(lifecycle).toHaveBeenCalledTimes(2);
      const types = lifecycle.mock.calls.map(
        ([ev]) => (ev as V2StreamEvent).type,
      );
      expect(types).toEqual(["stall_warning", "replanning"]);
    },
  );

  it("subscribes the every-channel-minus-debug default set on connect", () => {
    // Sanity-pin the on-connect channel set so a regression that
    // shrinks it (the v22 / v25 RC-3 root cause) fails loudly.
    // The backend's DEFAULT_SSE_CHANNELS = STANDARD_CHANNELS -
    // {"debug"} = 7 channels; the EventSource must have one
    // listener per channel attached pre-emptively.
    createV2Stream("org_default", { eventSourceFactory: fakeFactory });
    const attached = Array.from(factorySource!.listeners.keys())
      .filter((k) => k !== "error")
      .sort();
    expect(attached).toEqual([
      "checkpoints",
      "lifecycle",
      "messages",
      "progress_ledger",
      "tasks",
      "updates",
      "values",
    ]);
    expect(attached).not.toContain("stalls");
    expect(attached).not.toContain("replans");
    expect(attached).not.toContain("debug");
  });

  it("dispatches typed events to per-channel handlers", () => {
    const handler = vi.fn();
    const stream = createV2Stream("org_1", { eventSourceFactory: fakeFactory });
    stream.onEvent("progress_ledger", handler);

    factorySource!.emit("progress_ledger", {
      type: "ledger_emitted",
      payload: { is_progress_being_made: true, next_speaker: "writer" },
      org_id: "org_1",
      command_id: "cmd_1",
      superstep: 1,
      ts: "2026-05-18T00:00:00Z",
    });

    expect(handler).toHaveBeenCalledTimes(1);
    const ev = handler.mock.calls[0][0] as V2StreamEvent;
    expect(ev.type).toBe("ledger_emitted");
    expect(ev.payload.next_speaker).toBe("writer");
    stream.close();
  });

  it("does not deliver events from one channel to handlers of another", () => {
    const ledger = vi.fn();
    const messages = vi.fn();
    const stream = createV2Stream("org_2", { eventSourceFactory: fakeFactory });
    stream.onEvent("progress_ledger", ledger);
    stream.onEvent("messages", messages);

    factorySource!.emit("progress_ledger", {
      type: "ledger_emitted",
      payload: {},
      org_id: "org_2",
      command_id: "c",
      superstep: 1,
      ts: "t",
    });
    expect(ledger).toHaveBeenCalledTimes(1);
    expect(messages).not.toHaveBeenCalled();
    stream.close();
  });

  it("close() detaches every listener and closes the source", () => {
    const stream = createV2Stream("org_3", { eventSourceFactory: fakeFactory });
    expect(factorySource!.listeners.size).toBeGreaterThan(0);
    stream.close();
    expect(factorySource!.closed).toBe(true);
    // After close, no listeners remain registered.
    factorySource!.listeners.forEach((set) => expect(set.size).toBe(0));
  });

  it("invokes onError handlers when the source emits 'error'", () => {
    const onErr = vi.fn();
    const stream = createV2Stream("org_4", { eventSourceFactory: fakeFactory });
    stream.onError(onErr);
    factorySource!.emitError();
    expect(onErr).toHaveBeenCalledTimes(1);
    stream.close();
  });
});
