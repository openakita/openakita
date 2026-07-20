import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, act, waitFor, fireEvent } from "@testing-library/react";

const saveAttachment = vi.fn(async (..._args: unknown[]) => {});
vi.mock("../../platform", () => ({
  saveAttachment: (...a: unknown[]) => saveAttachment(...a),
  showInFolder: vi.fn(),
  openFileWithDefault: vi.fn(),
  IS_TAURI: false,
}));
vi.mock("../../platform/auth", () => ({ getAccessToken: () => "test-token" }));
vi.mock("../../views/chat/hooks/useMdModules", () => ({ useMdModules: () => null }));

const safeFetch = vi.fn(async (..._args: unknown[]) => ({
  ok: true,
  status: 200,
  text: async () => "# 标题\n\n正文内容ABC",
} as unknown as Response));
vi.mock("../../providers", () => ({ safeFetch: (...a: unknown[]) => safeFetch(...a) }));

import { FileAttachmentCard } from "../FileAttachmentCard";

describe("FileAttachmentCard preview (test18: HTML preview + PDF download/standalone)", () => {
  beforeEach(() => {
    saveAttachment.mockClear();
    safeFetch.mockClear();
  });

  it("opens a PDF standalone in a new tab (no embed, no modal, no download)", async () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { getByTitle } = render(
      <FileAttachmentCard
        file={{ filename: "最终报告.pdf", file_path: "D:/o/最终报告.pdf" }}
        apiBaseUrl="http://test"
      />,
    );
    // Primary click on a PDF => 独立查看 (open in a new tab), NOT a modal.
    const btn = getByTitle("点击独立查看（新标签）· 右键下载");
    await act(async () => { fireEvent.click(btn); });

    expect(openSpy).toHaveBeenCalledTimes(1);
    const url = String(openSpy.mock.calls[0][0]);
    expect(url).toContain("inline=1");
    expect(url).toContain("token=test-token");
    // No embedded PDF anywhere, and no download triggered by a preview click.
    expect(document.body.querySelector("iframe")).toBeNull();
    expect(document.body.querySelector("canvas")).toBeNull();
    expect(saveAttachment).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it("previews markdown as rendered HTML in a full-screen modal (not a download)", async () => {
    const { getByTitle } = render(
      <FileAttachmentCard
        file={{ filename: "报告.md", file_path: "D:/o/报告.md" }}
        apiBaseUrl="http://test"
      />,
    );
    const btn = getByTitle("点击预览 · 右键更多操作");
    await act(async () => { fireEvent.click(btn); });

    // Modal is portaled to document.body and shows the fetched markdown content.
    await waitFor(() => {
      expect(document.body.textContent).toContain("正文内容ABC");
    });
    // Rendered through the HTML markdown container, not a raw download.
    expect(document.body.querySelector(".file-preview-md")).not.toBeNull();
    expect(safeFetch).toHaveBeenCalled();
    expect(saveAttachment).not.toHaveBeenCalled();
  });

  it("portals the context menu so viewport coordinates survive transformed drawers", () => {
    const { getByTitle, getByText } = render(
      <div style={{ transform: "translateX(40%)" }}>
        <FileAttachmentCard
          file={{ filename: "报告.md", file_path: "D:/o/报告.md" }}
          apiBaseUrl="http://test"
        />
      </div>,
    );

    fireEvent.contextMenu(getByTitle("点击预览 · 右键更多操作"), {
      clientX: 137,
      clientY: 211,
    });

    const menu = getByText("下载文件").parentElement;
    expect(menu?.parentElement).toBe(document.body);
    expect(menu?.style.left).toBe("137px");
    expect(menu?.style.top).toBe("211px");
  });
});
