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
vi.mock("../../platform", () => ({
  onWsEvent: () => () => {},
  saveAttachment: vi.fn(),
  showInFolder: vi.fn(),
  openFileWithDefault: vi.fn(),
  IS_TAURI: false,
}));
vi.mock("../../platform/auth", () => ({ getAccessToken: () => null }));
vi.mock("../../utils/clipboard", () => ({ copyToClipboard: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// Two commands. The EARLIER command_id gets a LATER /activity ts (order must
// still follow command_id -- issue b). Each command's final report is present
// BOTH as an authoritative /commands result (with a deliverable) AND as a
// /history echo (the plain assistant reconstruction with the same 📋 heading
// but no attachments). The reconcile must keep ONE report per command -- the
// authoritative one -- and never show the echo (issue c).
const CID_EARLY = "cmd_1000000000000_00000000_aaaaaa";
const CID_LATE = "cmd_2000000000000_00000000_bbbbbb";
const BODY_EARLY = "正文甲UNIQE111";
const BODY_LATE = "正文乙UNIQL222";
const HEAD = "### 📋 任务完成汇报";

vi.mock("../../providers", () => ({
  safeFetch: vi.fn((url: string) => {
    const u = String(url);
    const j = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response);
    if (u.includes("/history")) {
      // The bug source: /history reconstructs the final reports as plain
      // assistant echoes (no attachments, no manifest).
      return j({
        messages: [
          { id: "restored-0", role: "user", content: "指令甲(先发)", timestamp: 1000 },
          { id: "restored-1", role: "assistant", content: `${HEAD}\n\n${BODY_EARLY}`, timestamp: 1001 },
          { id: "restored-2", role: "user", content: "指令乙(后发)", timestamp: 2000 },
          { id: "restored-3", role: "assistant", content: `${HEAD}\n\n${BODY_LATE}`, timestamp: 2001 },
        ],
      });
    }
    if (u.includes("/activity")) {
      return j({
        items: [
          { id: "u1", type: "user_command", command_id: CID_EARLY, content: "指令甲(先发)", ts: 9000 },
          { id: "u2", type: "user_command", command_id: CID_LATE, content: "指令乙(后发)", ts: 3000 },
        ],
      });
    }
    if (u.includes("/events")) {
      return j([
        { type: "file_output_registered", command_id: CID_EARLY, path: "D:/o/报告甲_终稿.pdf", size_bytes: 1234 },
        { type: "file_output_registered", command_id: CID_LATE, path: "D:/o/报告乙_终稿.pdf", size_bytes: 5678 },
      ]);
    }
    if (u.includes(`/commands/${CID_EARLY}`)) return j({ status: "done", result: { final_message: BODY_EARLY } });
    if (u.includes(`/commands/${CID_LATE}`)) return j({ status: "done", result: { final_message: BODY_LATE } });
    return j({});
  }),
}));

import { OrgChatPanel } from "../OrgChatPanel";

const count = (hay: string, needle: string) => hay.split(needle).length - 1;

describe("OrgChatPanel — online rebuild reconciliation (test18 a/b/c)", () => {
  it("keeps ONE authoritative report per command (no /history echo dup) and stable command_id order", async () => {
    const { container } = render(
      <OrgChatPanel orgId="org_rc" nodeId={null} apiBaseUrl="http://test" runtime="v2" />,
    );
    await act(async () => { await new Promise(r => setTimeout(r, 80)); });

    await waitFor(() => {
      expect(container.querySelectorAll(".ocp-cmd-block").length).toBe(2);
    });

    // (b) order: earlier command_id first even though its /activity ts is later.
    const blocks = Array.from(container.querySelectorAll(".ocp-cmd-block"));
    expect(blocks[0].getAttribute("data-command-id")).toBe(CID_EARLY);
    expect(blocks[1].getAttribute("data-command-id")).toBe(CID_LATE);

    // (c) each report body appears EXACTLY once -- the /history echo was dropped
    // so only the authoritative /commands bubble survives.
    const text = container.textContent || "";
    expect(count(text, BODY_EARLY)).toBe(1);
    expect(count(text, BODY_LATE)).toBe(1);

    // (a) the authoritative report bubble is present, with its deliverable
    // attachment attached (proving the report survives reload AND owns the file).
    expect(text).toContain("报告甲_终稿.pdf");
    expect(text).toContain("报告乙_终稿.pdf");
  });
});
