import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";

// Worker url import -> a plain string in tests.
vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({ default: "pdf.worker.js" }));

const fakePage = {
  getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
  render: () => ({ promise: Promise.resolve() }),
};
const fakePdf = { numPages: 3, getPage: vi.fn(async () => fakePage), destroy: vi.fn() };
const getDocument = vi.fn(() => ({ promise: Promise.resolve(fakePdf) }));
vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: (...a: unknown[]) => getDocument(...(a as [])),
}));

const safeFetch = vi.fn(async () => ({
  ok: true,
  status: 200,
  arrayBuffer: async () => new ArrayBuffer(2048),
} as unknown as Response));
vi.mock("../../providers", () => ({ safeFetch: (...a: unknown[]) => safeFetch(...(a as [string])) }));

import { PdfCanvasViewer } from "../PdfCanvasViewer";

describe("PdfCanvasViewer (test18 d)", () => {
  beforeEach(() => {
    safeFetch.mockClear();
    getDocument.mockClear();
    // jsdom canvas has no 2d context; the component only needs a non-null ctx.
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ scale: vi.fn() })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });

  it("fetches the PDF via the authed safeFetch and renders every page to canvas (pdf.js, no native iframe)", async () => {
    const { container } = render(<PdfCanvasViewer url="http://test/api/files?path=x&inline=1" />);

    await waitFor(() => {
      const root = container.querySelector('[data-testid="pdf-canvas-viewer"]');
      expect(root?.getAttribute("data-pdf-status")).toBe("ready");
    });

    // Authenticated fetch (not a bare iframe src) against the inline URL.
    expect(safeFetch).toHaveBeenCalledWith("http://test/api/files?path=x&inline=1");
    // getDocument was fed the fetched bytes, not a url (so auth is guaranteed).
    expect(getDocument).toHaveBeenCalledWith({ data: expect.any(ArrayBuffer) });
    // One <canvas> per page was painted.
    const root = container.querySelector('[data-testid="pdf-canvas-viewer"]');
    expect(root?.getAttribute("data-pdf-pages")).toBe("3");
    expect(container.querySelectorAll("canvas[data-pdf-page]").length).toBe(3);
  });

  it("surfaces an error (not a silent blank) when the fetch fails", async () => {
    safeFetch.mockResolvedValueOnce({ ok: false, status: 401 } as unknown as Response);
    const { container } = render(<PdfCanvasViewer url="http://test/api/files?path=y&inline=1" />);
    await waitFor(() => {
      const root = container.querySelector('[data-testid="pdf-canvas-viewer"]');
      expect(root?.getAttribute("data-pdf-status")).toBe("error");
    });
  });
});
