import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, act, waitFor, fireEvent } from "@testing-library/react";

const saveAttachment = vi.fn(async () => {});
vi.mock("../../platform", () => ({
  saveAttachment: (...a: unknown[]) => saveAttachment(...a),
  showInFolder: vi.fn(),
  openFileWithDefault: vi.fn(),
  IS_TAURI: false,
}));
vi.mock("../../platform/auth", () => ({ getAccessToken: () => "test-token" }));
vi.mock("../../views/chat/hooks/useMdModules", () => ({ useMdModules: () => null }));

// Stub the pdf.js viewer so this test focuses on the card's preview wiring
// (the real pdf.js render is covered by PdfCanvasViewer.test.tsx + the headless
// Chromium render evidence).
vi.mock("../PdfCanvasViewer", () => ({
  PdfCanvasViewer: ({ url }: { url: string }) => (
    <div data-testid="pdf-canvas-viewer-stub" data-url={url} />
  ),
}));

const safeFetch = vi.fn(async () => ({ ok: true, status: 200 } as unknown as Response));
vi.mock("../../providers", () => ({ safeFetch: (...a: unknown[]) => safeFetch(...(a as [string])) }));

import { FileAttachmentCard } from "../FileAttachmentCard";

describe("FileAttachmentCard PDF preview (test18)", () => {
  beforeEach(() => {
    saveAttachment.mockClear();
    safeFetch.mockClear();
  });

  it("previews a PDF with the pdf.js canvas viewer (not a native iframe) and does NOT download", async () => {
    const { getByTitle } = render(
      <FileAttachmentCard
        file={{ filename: "最终报告.pdf", file_path: "D:/o/最终报告.pdf" }}
        apiBaseUrl="http://test"
      />,
    );
    // The primary click is preview (docKind), not download.
    const previewBtn = getByTitle("点击预览 · 右键更多操作");
    await act(async () => { fireEvent.click(previewBtn); });

    // The preview modal is portaled to document.body.
    await waitFor(() => {
      const viewer = document.body.querySelector('[data-testid="pdf-canvas-viewer-stub"]');
      expect(viewer).not.toBeNull();
      // The pdf.js viewer is handed the inline URL (it fetches + renders itself,
      // authed, via canvas -- no native iframe PDF plugin dependency).
      const url = viewer?.getAttribute("data-url") || "";
      expect(url).toContain("inline=1");
      expect(url).not.toMatch(/^blob:/);
    });
    // No native PDF iframe.
    expect(document.body.querySelector("iframe")).toBeNull();
    // Preview must never trigger a download.
    expect(saveAttachment).not.toHaveBeenCalled();
  });
});
