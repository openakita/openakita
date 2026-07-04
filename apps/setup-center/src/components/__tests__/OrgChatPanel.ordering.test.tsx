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

// The org transcript is persisted to the session /history as plain {role,content}.
// On refresh /history therefore re-emits the user instruction AND the composed
// final report as loose bubbles (fresh random ids, no kind/commandId) -- the
// exact echoes that used to duplicate the result and scramble the order.
const REPORT_A = "### 📋 任务完成汇报\n\n汇报甲内容";
const REPORT_B = "### 📋 任务完成汇报\n\n汇报乙内容";
vi.mock("../../providers", () => ({
  safeFetch: vi.fn((url: string) => {
    const u = String(url);
    const j = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response);
    if (u.includes("/history")) {
      return j({
        messages: [
          { role: "user", content: "指令甲", timestamp: 1_000_000 },
          { role: "assistant", content: REPORT_A, timestamp: 1_500_000 },
          { role: "user", content: "指令乙", timestamp: 2_000_000 },
          { role: "assistant", content: REPORT_B, timestamp: 2_500_000 },
        ],
      });
    }
    if (u.includes("/activity")) {
      return j({
        items: [
          { id: "u1", type: "user_command", command_id: "cmd_a", content: "指令甲", ts: 1000 },
          { id: "u2", type: "user_command", command_id: "cmd_b", content: "指令乙", ts: 2000 },
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

function countOccurrences(hay: string, needle: string): number {
  let n = 0, i = 0;
  for (;;) {
    const at = hay.indexOf(needle, i);
    if (at < 0) return n;
    n += 1; i = at + needle.length;
  }
}

describe("OrgChatPanel — stable order + no duplicate result (test17 issue B)", () => {
  it("shows each command's result exactly once and keeps blocks in creation order despite /history echoes", async () => {
    const { container } = render(
      <OrgChatPanel orgId="org_ord" nodeId={null} apiBaseUrl="http://test" runtime="v2" />,
    );
    await act(async () => { await new Promise(r => setTimeout(r, 60)); });

    await waitFor(() => {
      expect(container.querySelectorAll(".ocp-cmd-block").length).toBe(2);
    });

    const blocks = Array.from(container.querySelectorAll(".ocp-cmd-block"));
    // Order is by command creation, stable: cmd_a before cmd_b.
    expect(blocks[0].getAttribute("data-command-id")).toBe("cmd_a");
    expect(blocks[1].getAttribute("data-command-id")).toBe("cmd_b");

    // Each result appears EXACTLY once across the whole panel -- the /history
    // echo of the report must not render a second copy.
    const all = container.textContent || "";
    expect(countOccurrences(all, "汇报甲内容")).toBe(1);
    expect(countOccurrences(all, "汇报乙内容")).toBe(1);

    // The later command's result must not float above the earlier one.
    expect(all.indexOf("汇报甲内容")).toBeLessThan(all.indexOf("汇报乙内容"));
    // No stray loose bubble outside the two command blocks duplicating a result.
    const looseReportish = Array.from(container.querySelectorAll(".ocp-msg"))
      .filter(el => !el.closest(".ocp-cmd-block") && /汇报[甲乙]内容/.test(el.textContent || ""));
    expect(looseReportish.length).toBe(0);
  });
});
