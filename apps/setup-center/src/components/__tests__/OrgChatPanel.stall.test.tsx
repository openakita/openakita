import { describe, expect, it, vi } from "vitest";
import { render, act } from "@testing-library/react";

// Capture the v2 stream lifecycle handler so the test can drive real
// agent-pipeline events (agent_run_started / node_tool_called / node_tool_failed).
let lifecycleHandler: ((ev: unknown) => void) | null = null;
vi.mock("../../api/v2Stream", () => {
  const onEvent = vi.fn((channel: string, handler: (ev: unknown) => void) => {
    if (channel === "lifecycle") lifecycleHandler = handler;
    return () => {};
  });
  const createV2Stream = vi.fn(() => ({
    onEvent,
    onError: vi.fn(() => () => {}),
    close: vi.fn(),
    url: "",
    readyState: 1,
  }));
  return { __esModule: true, createV2Stream };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === "string" ? d : k) }),
}));
vi.mock("../../views/chat/hooks/useMdModules", () => ({ useMdModules: () => null }));
vi.mock("../../platform", () => ({ onWsEvent: () => () => {} }));
vi.mock("../../providers", () => ({
  safeFetch: vi.fn(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ messages: [], items: [] }) } as unknown as Response),
  ),
}));
vi.mock("../../utils/clipboard", () => ({ copyToClipboard: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import { OrgChatPanel } from "../OrgChatPanel";

function emitLifecycle(type: string, payload: Record<string, unknown>) {
  lifecycleHandler?.({
    event_id: `${type}:${payload.node_id}:${Math.random()}`,
    command_id: "cmd_stall_1",
    superstep: 1,
    ts: String(Date.now()),
    type,
    payload,
  });
}

describe("OrgChatPanel — tool failures during active execution are not 进展缓慢 (test17 图2)", () => {
  it("keeps a node 进行中 (not stalled/collapsed) when a read_file/web_fetch tool call fails", async () => {
    render(<OrgChatPanel orgId="org_stall" nodeId={null} apiBaseUrl="http://test" runtime="v2" />);
    await act(async () => { await Promise.resolve(); });
    expect(lifecycleHandler).toBeTruthy();

    await act(async () => {
      emitLifecycle("agent_run_started", { node_id: "data-analyst", content_preview: "调研 AI 沙龙" });
      emitLifecycle("node_tool_called", { node_id: "data-analyst", tool_name: "read_file", args_preview: "report.md" });
      // a flaky read / network fetch failure mid-run
      emitLifecycle("node_tool_failed", { node_id: "data-analyst", tool_name: "web_fetch", reason: "network timeout" });
    });

    const timeline = document.querySelector('[data-testid="ocp-v2-timeline"]');
    expect(timeline).toBeTruthy();
    const text = timeline?.textContent || "";
    // CORE FIX: a single failed tool call must NOT read as a stall. (Before the
    // fix this segment showed "进展缓慢"; now the node keeps making progress.)
    expect(text).not.toContain("进展缓慢");
    // and the failure detail is still surfaced (informational), not swallowed.
    expect(text).toContain("web_fetch");
  });

  it("still surfaces a genuine node-level failure as 失败", async () => {
    lifecycleHandler = null;
    render(<OrgChatPanel orgId="org_stall2" nodeId={null} apiBaseUrl="http://test" runtime="v2" />);
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      emitLifecycle("agent_run_started", { node_id: "writer", content_preview: "写作" });
      emitLifecycle("agent_run_failed", { node_id: "writer" });
    });

    const timeline = document.querySelector('[data-testid="ocp-v2-timeline"]');
    const text = timeline?.textContent || "";
    expect(text).toContain("失败");
  });
});
