import { describe, expect, it, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";

// Mock the v2 stream module BEFORE importing the panel so the
// panel's useEffect picks up the spy.
vi.mock("../../api/v2Stream", () => {
  const calls: { orgId: string; opts: unknown }[] = [];
  let lastHandler: ((ev: unknown) => void) | null = null;
  const closeFn = vi.fn();
  const offFn = vi.fn();
  const onEvent = vi.fn((channel: string, handler: (ev: unknown) => void) => {
    if (channel === "progress_ledger") lastHandler = handler;
    return offFn;
  });
  const createV2Stream = vi.fn((orgId: string, opts?: unknown) => {
    calls.push({ orgId, opts });
    return { onEvent, onError: vi.fn(() => () => {}), close: closeFn, url: "", readyState: 1 };
  });
  return {
    __esModule: true,
    createV2Stream,
    // expose internals so the test can drive the handler
    _calls: calls,
    _emitProgress: (payload: Record<string, unknown>) => {
      if (lastHandler) {
        lastHandler({
          event_id: "ev_x",
          command_id: "cmd_x",
          superstep: 1,
          ts: "2026-05-18T01:23:45Z",
          type: "ledger_emitted",
          payload,
          org_id: "org_test",
        });
      }
    },
    _closeFn: closeFn,
  };
});

// The panel pulls heavy modules (i18next, useMdModules, sonner, ...).
// We mock them with no-op shims so the component renders in jsdom
// without bootstrapping the rest of the app.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
vi.mock("../../views/chat/hooks/useMdModules", () => ({
  useMdModules: () => null,
}));
vi.mock("../../platform", () => ({
  onWsEvent: () => () => {},
}));
vi.mock("../../providers", () => ({
  safeFetch: vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ messages: [], pending_command: null }),
    } as unknown as Response),
  ),
}));
vi.mock("../../utils/clipboard", () => ({ copyToClipboard: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import * as v2StreamModule from "../../api/v2Stream";
import { OrgChatPanel } from "../OrgChatPanel";

describe("OrgChatPanel v1/v2 dispatch", () => {
  it("does NOT instantiate v2 stream for the legacy v1 path", () => {
    render(<OrgChatPanel orgId="org_v1" apiBaseUrl="http://test" />);
    expect(v2StreamModule.createV2Stream).not.toHaveBeenCalled();
    expect(screen.queryByTestId("ocp-v2-timeline")).toBeNull();
  });

  it("mounts the timeline and subscribes when runtime=v2", async () => {
    render(<OrgChatPanel orgId="org_v2" apiBaseUrl="http://test" runtime="v2" />);
    expect(v2StreamModule.createV2Stream).toHaveBeenCalledWith(
      "org_v2",
      { apiBase: "http://test" },
    );
    // The live-process feed now lives inside the message column and only
    // appears once there is at least one event (no permanent empty banner).
    expect(screen.queryByTestId("ocp-v2-timeline")).toBeNull();

    // Drive a progress_ledger event through the captured handler
    // and assert the timeline renders the new entry.
    await act(async () => {
      (v2StreamModule as unknown as {
        _emitProgress: (p: Record<string, unknown>) => void;
      })._emitProgress({
        is_request_satisfied: false,
        is_in_loop: false,
        is_progress_being_made: true,
        next_speaker: "writer",
        instruction_or_question: "draft synopsis",
      });
    });
    expect(screen.getByTestId("ocp-v2-timeline")).toBeInTheDocument();
    expect(screen.getByText("writer")).toBeInTheDocument();
    expect(screen.getByText("draft synopsis")).toBeInTheDocument();
  });
});
