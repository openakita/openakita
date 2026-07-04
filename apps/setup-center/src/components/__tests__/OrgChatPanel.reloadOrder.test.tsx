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

// The command_id embeds its creation epoch-ms. Here the EARLIER command
// (cmd_1000000000000) is deliberately given a LATER /activity timestamp than
// the LATER command (cmd_2000000000000). Ordering must follow the command_id
// (stable across live/reload/refresh -- issue 1), NOT the reload timestamp.
const CID_EARLY = "cmd_1000000000000_00000000_aaaaaa";
const CID_LATE = "cmd_2000000000000_00000000_bbbbbb";
vi.mock("../../providers", () => ({
  safeFetch: vi.fn((url: string) => {
    const u = String(url);
    const j = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response);
    if (u.includes("/history")) return j({ messages: [] });
    if (u.includes("/activity")) {
      return j({
        items: [
          // reversed timestamps relative to command_id creation order
          { id: "u1", type: "user_command", command_id: CID_EARLY, content: "指令甲(先发)", ts: 9000 },
          { id: "u2", type: "user_command", command_id: CID_LATE, content: "指令乙(后发)", ts: 3000 },
        ],
      });
    }
    if (u.includes("/events")) return j([]);
    if (u.includes(`/commands/${CID_EARLY}`)) return j({ status: "done", result: { final_message: "汇报甲" } });
    if (u.includes(`/commands/${CID_LATE}`)) return j({ status: "done", result: { final_message: "汇报乙" } });
    return j({});
  }),
}));

import { OrgChatPanel } from "../OrgChatPanel";

describe("OrgChatPanel — reload order follows command_id (test17 item 1)", () => {
  it("orders blocks by command_id creation time regardless of reload timestamps", async () => {
    const { container } = render(
      <OrgChatPanel orgId="org_ro" nodeId={null} apiBaseUrl="http://test" runtime="v2" />,
    );
    await act(async () => { await new Promise(r => setTimeout(r, 60)); });

    await waitFor(() => {
      expect(container.querySelectorAll(".ocp-cmd-block").length).toBe(2);
    });
    const blocks = Array.from(container.querySelectorAll(".ocp-cmd-block"));
    // Earlier command_id first, even though its /activity ts is larger.
    expect(blocks[0].getAttribute("data-command-id")).toBe(CID_EARLY);
    expect(blocks[1].getAttribute("data-command-id")).toBe(CID_LATE);
  });
});
