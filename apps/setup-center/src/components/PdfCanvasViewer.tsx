import { useEffect, useRef, useState } from "react";
import { safeFetch } from "../providers";
// Vite emits the worker as a hashed asset and hands us its URL. pdf.js needs a
// dedicated worker; without this the main thread blocks / fails to parse.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

/**
 * test18 (d): render a PDF with pdf.js into <canvas> elements instead of relying
 * on the browser's native in-<iframe> PDF viewer.
 *
 * Root cause of the web-path blank preview: the backend serves a perfectly
 * valid inline PDF (``Content-Type: application/pdf`` + ``Content-Disposition:
 * inline``, no ``X-Frame-Options`` / CSP), but rendering it inside an <iframe>
 * depends on the browser's built-in PDF plugin, which is unreliable across
 * environments -- e.g. Chrome's "Download PDF files instead of automatically
 * opening them" setting, a disabled/absent PDF plugin, or blob-URL handling --
 * and fails SILENTLY to a grey blank with no error. The media-strategy plugin
 * sidesteps native PDF rendering entirely (it renders HTML via ``srcdoc``), so
 * it never hits this. pdf.js is pure JS: it parses the bytes we fetch (through
 * the AUTHENTICATED ``safeFetch`` so the bearer token is attached) and paints
 * to canvas, so it renders in any browser AND can be asserted in headless
 * Chromium.
 */
export function PdfCanvasViewer({ url }: { url: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [numPages, setNumPages] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let pdfDoc: { numPages: number; getPage: (n: number) => Promise<unknown>; destroy: () => void } | null = null;

    (async () => {
      setStatus("loading");
      setError(null);
      setNumPages(0);
      try {
        // Authenticated fetch -> ArrayBuffer. The iframe could not carry the
        // bearer token; safeFetch does, so this works in web/online mode too.
        const res = await safeFetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.arrayBuffer();
        if (data.byteLength === 0) throw new Error("empty PDF response");

        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const task = pdfjs.getDocument({ data });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        pdfDoc = (await task.promise) as any;
        if (cancelled || !pdfDoc) return;
        setNumPages(pdfDoc.numPages);

        const container = containerRef.current;
        if (!container) return;
        container.innerHTML = "";
        const cssWidth = Math.max(320, (container.clientWidth || 800) - 24);
        const dpr = Math.min(window.devicePixelRatio || 1, 2);

        for (let n = 1; n <= pdfDoc.numPages; n++) {
          if (cancelled) return;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const page = (await pdfDoc.getPage(n)) as any;
          const base = page.getViewport({ scale: 1 });
          const scale = cssWidth / base.width;
          const viewport = page.getViewport({ scale });
          const canvas = document.createElement("canvas");
          canvas.width = Math.floor(viewport.width * dpr);
          canvas.height = Math.floor(viewport.height * dpr);
          canvas.style.width = `${Math.floor(viewport.width)}px`;
          canvas.style.height = `${Math.floor(viewport.height)}px`;
          canvas.style.display = "block";
          canvas.style.margin = "0 auto 12px";
          canvas.style.background = "#fff";
          canvas.style.boxShadow = "0 1px 8px rgba(0,0,0,0.35)";
          canvas.setAttribute("data-pdf-page", String(n));
          container.appendChild(canvas);
          const ctx = canvas.getContext("2d");
          if (!ctx) throw new Error("canvas 2d context unavailable");
          const transform = dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined;
          await page.render({ canvasContext: ctx, viewport, transform }).promise;
        }
        if (!cancelled) setStatus("ready");
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setStatus("error");
        }
      }
    })();

    return () => {
      cancelled = true;
      if (pdfDoc) {
        try { pdfDoc.destroy(); } catch { /* ignore */ }
      }
    };
  }, [url]);

  return (
    <div
      style={{ width: "100%", height: "100%", overflow: "auto", background: "#525659", padding: 12 }}
      data-testid="pdf-canvas-viewer"
      data-pdf-status={status}
      data-pdf-pages={numPages}
    >
      {status === "loading" && (
        <div style={{ padding: 24, color: "#e5e7eb", fontSize: 13, textAlign: "center" }}>
          正在渲染 PDF…
        </div>
      )}
      {status === "error" && (
        <div style={{ padding: 24, color: "#fbbf24", fontSize: 13, textAlign: "center" }}>
          PDF 渲染失败（{error}）。你可以改为下载后查看。
        </div>
      )}
      <div ref={containerRef} />
    </div>
  );
}
