import { describe, expect, it, vi } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";

// --- v2 stream shim (same shape as OrgChatPanel.v2.test) --------------------
vi.mock("../../api/v2Stream", () => {
  const onEvent = vi.fn(() => () => {});
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
  // return the defaultValue when provided so the report heading is human text
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === "string" ? d : k) }),
}));
vi.mock("../../views/chat/hooks/useMdModules", () => ({ useMdModules: () => null }));
vi.mock("../../utils/clipboard", () => ({ copyToClipboard: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// --- WS bus mock: collect every subscriber, let the test broadcast ----------
const _wsHandlers: Array<(evt: string, raw: unknown) => void> = [];
function emitWs(evt: string, raw: unknown) {
  for (const h of [..._wsHandlers]) h(evt, raw);
}
vi.mock("../../platform", () => ({
  onWsEvent: (h: (evt: string, raw: unknown) => void) => {
    _wsHandlers.push(h);
    return () => {
      const i = _wsHandlers.indexOf(h);
      if (i >= 0) _wsHandlers.splice(i, 1);
    };
  },
}));

const REPORT_MARKER = "UNIQUE_ROOT_SUMMARY_MARKER_测试17";
const CID = "cmd_test_final_1";

// --- safeFetch router -------------------------------------------------------
vi.mock("../../providers", () => ({
  safeFetch: vi.fn((url: string, init?: RequestInit) => {
    const u = String(url);
    const jsonOf = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response);
    if (u.includes("/history")) return jsonOf({ messages: [], pending_command: null });
    if (u.includes("/activity")) return jsonOf({ items: [] });
    if (u.includes("/events")) return jsonOf([]);
    if (u.endsWith("/command") && init?.method === "POST") return jsonOf({ command_id: CID });
    if (u.includes(`/commands/${CID}`)) {
      return jsonOf({
        status: "done",
        result: { final_message: REPORT_MARKER, deliverable: REPORT_MARKER, partial: false, outcome: "done" },
      });
    }
    if (u.includes("/messages")) return jsonOf({ ok: true });
    return jsonOf({});
  }),
}));

import { OrgChatPanel } from "../OrgChatPanel";

describe("OrgChatPanel — standalone final-report bubble (test17)", () => {
  it("renders a separate final-report bubble on command_done and keeps it after a LATE event", async () => {
    render(
      <OrgChatPanel orgId="org_final" nodeId={null} apiBaseUrl="http://test" runtime="v2" />,
    );

    // Wait for the initial history load to settle.
    await act(async () => { await Promise.resolve(); });

    const textarea = document.querySelector("textarea.ocp-textarea") as HTMLTextAreaElement;
    expect(textarea).toBeTruthy();
    fireEvent.change(textarea, { target: { value: "策划一份沙龙方案" } });
    const sendBtn = document.querySelector("button.ocp-send") as HTMLButtonElement;
    expect(sendBtn).toBeTruthy();

    await act(async () => {
      fireEvent.click(sendBtn);
      // let the POST /command resolve so the WS listener is registered
      await Promise.resolve();
      await Promise.resolve();
    });

    // Broadcast the real terminal event: the root produced a full report.
    await act(async () => {
      emitWs("org:command_done", {
        org_id: "org_final",
        command_id: CID,
        status: "done",
        result: { final_message: REPORT_MARKER, deliverable: REPORT_MARKER, partial: false, outcome: "done" },
      });
      // the live handler finalizes inside a 500ms setTimeout
      await new Promise(r => setTimeout(r, 650));
    });

    // The standalone bottom report bubble must exist and carry the report text.
    await waitFor(() => {
      const bubble = document.querySelector(".ocp-msg-final_report");
      expect(bubble).toBeTruthy();
      expect(bubble?.textContent || "").toContain(REPORT_MARKER);
    });

    // REGRESSION: a late progress event (fires seconds after command_done in
    // real runs, e.g. final_report_pdf / node idle) used to call updatePreview
    // and overwrite the finalized placeholder back to "组织正在处理中…",
    // making the report vanish. It must NOT touch the standalone bubble now.
    await act(async () => {
      emitWs("org:node_status", { org_id: "org_final", node_id: "editor-in-chief", status: "idle", exit_reason: "normal" });
      emitWs("org:file_output_registered", {
        org_id: "org_final", node_id: "editor-in-chief", command_id: CID,
        memory_type: "resource", filename: "report.pdf", file_path: "/x/report.pdf", file_size: 1,
      });
      await new Promise(r => setTimeout(r, 50));
    });

    const bubbleAfter = document.querySelector(".ocp-msg-final_report");
    expect(bubbleAfter).toBeTruthy();
    expect(bubbleAfter?.textContent || "").toContain(REPORT_MARKER);
    // and it must NOT have been reverted to the live "processing" placeholder text
    expect(bubbleAfter?.textContent || "").not.toContain("组织正在处理中");
  });
});
