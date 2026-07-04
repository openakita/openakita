import { describe, expect, it, vi } from "vitest";
import { render, act, waitFor } from "@testing-library/react";

vi.mock("../../api/v2Stream", () => {
  const onEvent = vi.fn(() => () => {});
  const createV2Stream = vi.fn(() => ({
    onEvent, onError: vi.fn(() => () => {}), close: vi.fn(), url: "", readyState: 1,
  }));
  return { __esModule: true, createV2Stream };
});
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === "string" ? d : k) }),
}));
vi.mock("../../views/chat/hooks/useMdModules", () => ({ useMdModules: () => null }));
vi.mock("../../platform", () => ({ onWsEvent: () => () => {} }));
vi.mock("../../utils/clipboard", () => ({ copyToClipboard: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// Two prior commands persisted in the org: cmd_a (earlier) then cmd_b (later).
vi.mock("../../providers", () => ({
  safeFetch: vi.fn((url: string) => {
    const u = String(url);
    const j = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response);
    if (u.includes("/history")) return j({ messages: [], pending_command: null });
    if (u.includes("/activity")) {
      return j({
        items: [
          { id: "u1", type: "user_command", command_id: "cmd_a", content: "指令甲：做方案", ts: 1000 },
          { id: "u2", type: "user_command", command_id: "cmd_b", content: "指令乙：补文案", ts: 2000 },
        ],
      });
    }
    if (u.includes("/events")) return j([]);
    if (u.includes("/commands/cmd_a")) return j({ status: "done", result: { final_message: "汇报甲内容" } });
    if (u.includes("/commands/cmd_b")) return j({ status: "done", result: { final_message: "汇报乙内容" } });
    return j({});
  }),
}));

import { OrgChatPanel } from "../OrgChatPanel";

describe("OrgChatPanel — multi-command history rebuild (test17 Task3)", () => {
  it("rebuilds per-command blocks (用户指令 → 汇报) in chronological order after reload", async () => {
    const { container } = render(
      <OrgChatPanel orgId="org_hist" nodeId={null} apiBaseUrl="http://test" runtime="v2" />,
    );
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });

    await waitFor(() => {
      const blocks = container.querySelectorAll(".ocp-cmd-block");
      expect(blocks.length).toBe(2);
    });

    const blocks = Array.from(container.querySelectorAll(".ocp-cmd-block"));
    // Block order is chronological: cmd_a then cmd_b.
    expect(blocks[0].getAttribute("data-command-id")).toBe("cmd_a");
    expect(blocks[1].getAttribute("data-command-id")).toBe("cmd_b");
    // Each block pairs the user instruction with its OWN final report -- prior
    // commands' orchestration/summary is preserved, not collapsed into one.
    expect(blocks[0].textContent).toContain("指令甲：做方案");
    expect(blocks[0].textContent).toContain("汇报甲内容");
    expect(blocks[1].textContent).toContain("指令乙：补文案");
    expect(blocks[1].textContent).toContain("汇报乙内容");
    // and each block carries exactly one final-report bubble.
    expect(blocks[0].querySelectorAll(".ocp-msg-final_report").length).toBe(1);
    expect(blocks[1].querySelectorAll(".ocp-msg-final_report").length).toBe(1);
  });
});
