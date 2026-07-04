import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import { saveAttachment, showInFolder, openFileWithDefault, IS_TAURI } from "../platform";
import { getAccessToken } from "../platform/auth";
import { getFileTypeIcon, IconDownload } from "../icons";
import { safeFetch } from "../providers";
import { useMdModules } from "../views/chat/hooks/useMdModules";
import { MarkdownContent } from "../views/chat/components/MarkdownContent";

// When the app runs in web/online mode the backend requires auth. A plain
// <img>/<video>/<iframe> ``src`` cannot send the Authorization header, so the
// request 401s and the media (notably PDF) renders blank. The auth middleware
// explicitly accepts a ``?token=`` query param "for tags that can't set
// headers" -- append it so media tiles and the PDF fallback load. Returns the
// url unchanged in local Tauri mode (getAccessToken() is null there).
function withAuthToken(url: string): string {
  const token = getAccessToken();
  if (!token) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

export interface FileAttachment {
  filename: string;
  file_path: string;
  file_size?: number;
}

function fmtFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type MediaKind = "image" | "video" | "other";

const IMAGE_EXT = new Set([
  "png", "jpg", "jpeg", "webp", "gif", "bmp", "avif",
]);
const VIDEO_EXT = new Set([
  "mp4", "webm", "mov", "m4v", "ogv",
]);

function detectMediaKind(filename: string): MediaKind {
  const ix = filename.lastIndexOf(".");
  if (ix < 0) return "other";
  const ext = filename.slice(ix + 1).toLowerCase();
  if (IMAGE_EXT.has(ext)) return "image";
  if (VIDEO_EXT.has(ext)) return "video";
  return "other";
}

// Deliverables are essentially markdown or PDF (see issue: 交付物无非是 md 和 pdf).
// Detect them so the attachment card can offer an in-app 弹窗预览 in addition to
// the existing download, without forcing the user to download-then-open.
type DocKind = "markdown" | "text" | "pdf" | null;
const MD_EXT = new Set(["md", "markdown"]);
const TEXT_EXT = new Set(["txt", "text", "log", "csv"]);
function detectDocKind(filename: string): DocKind {
  const ix = filename.lastIndexOf(".");
  if (ix < 0) return null;
  const ext = filename.slice(ix + 1).toLowerCase();
  if (MD_EXT.has(ext)) return "markdown";
  if (ext === "pdf") return "pdf";
  if (TEXT_EXT.has(ext)) return "text";
  return null;
}

interface FileAttachmentCardProps {
  file: FileAttachment;
  apiBaseUrl: string;
  // P10: inline=true 时尺寸略缩，用于 OrgChatPanel timeline 内嵌；
  // 默认 false 是消息底部的常规附件展示。
  inline?: boolean;
}

export function FileAttachmentCard({ file, apiBaseUrl, inline = false }: FileAttachmentCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState({ x: 0, y: 0 });
  const [previewOpen, setPreviewOpen] = useState(false);
  const [mediaError, setMediaError] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const mdModules = useMdModules();

  const mediaKind = useMemo(() => detectMediaKind(file.filename), [file.filename]);
  const docKind = useMemo(() => detectDocKind(file.filename), [file.filename]);
  const mediaUrl = useMemo(
    () => `${apiBaseUrl}/api/files?path=${encodeURIComponent(file.file_path)}`,
    [apiBaseUrl, file.file_path],
  );
  // PDFs need an inline Content-Disposition to render inside an <iframe>;
  // the default (attachment) would trigger a download instead of a preview.
  const inlineUrl = useMemo(() => `${mediaUrl}&inline=1`, [mediaUrl]);
  // Authed url for <img>/<video> tags (they can't send the bearer header).
  const authedMediaUrl = useMemo(() => withAuthToken(mediaUrl), [mediaUrl]);

  // Doc (md/pdf/text) preview modal state.
  const [docPreviewOpen, setDocPreviewOpen] = useState(false);
  const [docText, setDocText] = useState<string | null>(null);
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);
  // 独立查看: open the PDF in a new browser tab (native viewer / download),
  // carrying the middleware ?token= so online auth passes. Following the
  // media-strategy direction, PDFs are NOT embedded in the preview modal --
  // only markdown/text render as styled HTML in-modal, which reads far better.
  const openInNewTab = useCallback(() => {
    window.open(withAuthToken(inlineUrl), "_blank", "noopener,noreferrer");
  }, [inlineUrl]);

  const openDocPreview = useCallback(async () => {
    if (!docKind) return;
    // PDF has no HTML preview -- open it standalone instead of a blank modal.
    if (docKind === "pdf") { openInNewTab(); return; }
    setDocPreviewOpen(true);
    if (docText !== null) return; // already loaded
    setDocLoading(true);
    setDocError(null);
    try {
      const res = await safeFetch(mediaUrl);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDocText(await res.text());
    } catch (e) {
      setDocError(e instanceof Error ? e.message : String(e));
    } finally {
      setDocLoading(false);
    }
  }, [docKind, docText, mediaUrl, openInNewTab]);

  const handleDownload = useCallback(async () => {
    try {
      await saveAttachment({
        apiUrl: mediaUrl,
        filename: file.filename,
      });
    } catch (e) {
      console.error("File save failed:", e);
    }
  }, [mediaUrl, file.filename]);

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setMenuPos({ x: e.clientX, y: e.clientY });
    setMenuOpen(true);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as HTMLElement)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  useEffect(() => {
    if (!previewOpen && !docPreviewOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setPreviewOpen(false); setDocPreviewOpen(false); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [previewOpen, docPreviewOpen]);

  const Icon = getFileTypeIcon(file.filename);

  const useMediaTile = (mediaKind === "image" || mediaKind === "video") && !mediaError;

  const contextMenu = menuOpen && (
    <div
      ref={menuRef}
      style={{
        position: "fixed", left: menuPos.x, top: menuPos.y, zIndex: 9999,
        background: "var(--bg-app, #1e293b)", border: "1px solid var(--line, rgba(100,116,139,0.3))",
        borderRadius: 6, boxShadow: "0 4px 16px rgba(0,0,0,0.3)",
        padding: 4, minWidth: 160, fontSize: 12,
      }}
    >
      <button
        style={menuItemStyle}
        onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
        onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
        onClick={() => { setMenuOpen(false); handleDownload(); }}
      >
        下载文件
      </button>
      {docKind === "pdf" ? (
        <button
          style={menuItemStyle}
          onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
          onClick={() => { setMenuOpen(false); openInNewTab(); }}
        >
          独立查看
        </button>
      ) : docKind ? (
        <button
          style={menuItemStyle}
          onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
          onClick={() => { setMenuOpen(false); void openDocPreview(); }}
        >
          预览
        </button>
      ) : null}
      {(mediaKind === "image" || mediaKind === "video") && (
        <button
          style={menuItemStyle}
          onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
          onClick={() => { setMenuOpen(false); setPreviewOpen(true); }}
        >
          {mediaKind === "image" ? "查看大图" : "全屏播放"}
        </button>
      )}
      {IS_TAURI && (
        <>
          <button
            style={menuItemStyle}
            onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
            onClick={() => { setMenuOpen(false); openFileWithDefault(file.file_path); }}
          >
            用默认应用打开
          </button>
          <button
            style={menuItemStyle}
            onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
            onClick={() => { setMenuOpen(false); showInFolder(file.file_path); }}
          >
            在文件管理器中显示
          </button>
        </>
      )}
    </div>
  );

  // test17 item 2: render the preview to a body-level portal. A plain
  // ``position: fixed`` element is clipped to the nearest ancestor that
  // establishes a containing block (any ``transform``/``filter``/``will-change``
  // on the blackboard drawer or side panel), which is exactly why the preview
  // used to be trapped inside the sidebar width. Portaling to ``document.body``
  // makes it a true full-viewport overlay everywhere (command center / blackboard
  // / projects) with identical behaviour.
  const docPreviewModal = docPreviewOpen && createPortal(
    <div
      onClick={() => setDocPreviewOpen(false)}
      style={{
        position: "fixed", inset: 0, zIndex: 10000,
        background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: "min(860px, 92vw)",
          height: "88vh", display: "flex", flexDirection: "column",
          background: "var(--bg-app, #0f172a)",
          border: "1px solid var(--line, rgba(100,116,139,0.3))",
          borderRadius: 10, overflow: "hidden",
          boxShadow: "0 12px 48px rgba(0,0,0,0.45)",
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "10px 14px", borderBottom: "1px solid var(--line, rgba(100,116,139,0.25))",
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 16, lineHeight: 1, flexShrink: 0 }}><Icon size={16} /></span>
          <span style={{
            flex: 1, fontSize: 13, fontWeight: 600, color: "var(--text)",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }} title={file.file_path}>{file.filename}</span>
          <button
            type="button"
            onClick={handleDownload}
            title="下载"
            style={{
              display: "flex", alignItems: "center", gap: 4,
              background: "rgba(8,145,178,0.12)", border: "1px solid rgba(8,145,178,0.25)",
              color: "#0891b2", borderRadius: 5, padding: "4px 8px", cursor: "pointer", fontSize: 12,
            }}
          >
            <IconDownload size={12} />
            下载
          </button>
          <button
            type="button"
            onClick={() => setDocPreviewOpen(false)}
            title="关闭"
            aria-label="关闭"
            style={{
              background: "none", border: "none", color: "var(--muted)",
              cursor: "pointer", fontSize: 20, lineHeight: 1, padding: "0 4px",
            }}
          >×</button>
        </div>
        <div style={{
          flex: 1, minHeight: 0, overflow: "auto",
          // Follow the app theme instead of a hard-coded dark surface. The old
          // ``var(--bg-card, #111827)`` used an UNDEFINED variable, so it always
          // fell back to near-black; on the light theme the (dark) --text was
          // then invisible against it (test18 图2). --panel2 is defined in every
          // theme and always contrasts with --text.
          background: "var(--panel2)",
        }}>
          {docKind === "pdf" ? (
            // Defensive: PDFs normally open in a new tab and never reach this
            // modal. If one does (e.g. a caller forces it open), degrade to a
            // clear placeholder with download + 独立查看 -- never a blank pane.
            <div style={{ padding: 28, color: "var(--text)", fontSize: 13, display: "flex", flexDirection: "column", gap: 12, alignItems: "flex-start" }}>
              <div>该文件为 PDF，暂不支持在弹窗内 HTML 预览。请下载或在新标签独立查看。</div>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" onClick={openInNewTab} style={docModalActionBtnStyle}>独立查看</button>
                <button type="button" onClick={handleDownload} style={docModalActionBtnStyle}>下载</button>
              </div>
            </div>
          ) : docLoading ? (
            <div style={{ padding: 24, color: "var(--muted)", fontSize: 13 }}>正在加载预览…</div>
          ) : docError ? (
            <div style={{ padding: 24, color: "#f59e0b", fontSize: 13 }}>
              预览加载失败（{docError}）。你可以改为下载后查看。
            </div>
          ) : docKind === "markdown" ? (
            <div className="chatMdContent file-preview-md" style={{ padding: "18px 22px", fontSize: 14, color: "var(--text)" }}>
              <MarkdownContent content={docText || ""} mdModules={mdModules} />
            </div>
          ) : (
            <pre style={{
              padding: "16px 20px", margin: 0, fontSize: 13, color: "var(--text)",
              whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "Consolas, Menlo, monospace",
            }}>{docText || ""}</pre>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );

  if (useMediaTile) {
    const maxW = inline ? 200 : 240;
    const maxH = inline ? 140 : 180;
    return (
      <>
        <div
          style={{
            display: "inline-flex", flexDirection: "column",
            gap: 4, padding: 4, borderRadius: 6,
            background: "rgba(8,145,178,0.06)",
            border: "1px solid rgba(8,145,178,0.18)",
            maxWidth: maxW + 8,
          }}
          title={file.file_path}
          onContextMenu={handleContextMenu}
        >
          {mediaKind === "image" ? (
            <img
              src={authedMediaUrl}
              alt={file.filename}
              loading="lazy"
              onError={() => setMediaError(true)}
              onClick={() => setPreviewOpen(true)}
              style={{
                maxWidth: maxW, maxHeight: maxH,
                width: "auto", height: "auto",
                borderRadius: 4, cursor: "zoom-in",
                objectFit: "contain", background: "rgba(0,0,0,0.15)",
              }}
            />
          ) : (
            <video
              src={authedMediaUrl}
              controls
              preload="metadata"
              onError={() => setMediaError(true)}
              style={{
                maxWidth: maxW, maxHeight: maxH,
                width: "auto", height: "auto",
                borderRadius: 4, background: "#000",
              }}
            />
          )}
          <div style={{
            display: "flex", alignItems: "center", gap: 4,
            fontSize: 11, color: "var(--muted)",
            maxWidth: maxW,
          }}>
            <span style={{
              flex: 1, overflow: "hidden",
              textOverflow: "ellipsis", whiteSpace: "nowrap",
              color: "var(--text)",
            }}>{file.filename}</span>
            {file.file_size != null && (
              <span style={{ flexShrink: 0 }}>{fmtFileSize(file.file_size)}</span>
            )}
            <button
              type="button"
              onClick={handleDownload}
              title="下载"
              style={{
                background: "none", border: "none", cursor: "pointer",
                color: "#0891b2", padding: 0, lineHeight: 1, flexShrink: 0,
              }}
            >
              <IconDownload size={12} />
            </button>
          </div>
        </div>
        {contextMenu}
        {previewOpen && createPortal(
          <div
            onClick={() => setPreviewOpen(false)}
            style={{
              position: "fixed", inset: 0, zIndex: 10000,
              background: "rgba(0,0,0,0.85)",
              display: "flex", alignItems: "center", justifyContent: "center",
              padding: 24, cursor: "zoom-out",
            }}
          >
            {mediaKind === "image" ? (
              <img
                src={authedMediaUrl}
                alt={file.filename}
                style={{ maxWidth: "95vw", maxHeight: "95vh", borderRadius: 4 }}
                onClick={e => e.stopPropagation()}
              />
            ) : (
              <video
                src={authedMediaUrl}
                controls
                autoPlay
                style={{ maxWidth: "95vw", maxHeight: "95vh", borderRadius: 4 }}
                onClick={e => e.stopPropagation()}
              />
            )}
          </div>,
          document.body,
        )}
      </>
    );
  }

  return (
    <>
      <div style={{ display: "flex", alignItems: "stretch", gap: 4, width: "100%" }}>
        <button
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 10px", borderRadius: 5,
            background: "rgba(8,145,178,0.08)",
            border: "1px solid rgba(8,145,178,0.2)",
            cursor: "pointer", flex: 1, minWidth: 0,
            textAlign: "left", fontSize: 12,
            transition: "background 0.15s",
          }}
          title={
            docKind === "pdf"
              ? "点击独立查看（新标签）· 右键下载"
              : docKind
                ? "点击预览 · 右键更多操作"
                : file.file_path
          }
          onMouseEnter={e => { e.currentTarget.style.background = "rgba(8,145,178,0.16)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "rgba(8,145,178,0.08)"; }}
          onClick={docKind ? () => { void openDocPreview(); } : handleDownload}
          onContextMenu={handleContextMenu}
        >
          <span style={{ fontSize: 16, lineHeight: 1, flexShrink: 0 }}>
            <Icon size={16} />
          </span>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text)" }}>
            {file.filename}
          </span>
          {file.file_size != null && (
            <span style={{ fontSize: 11, color: "var(--muted)", flexShrink: 0 }}>
              {fmtFileSize(file.file_size)}
            </span>
          )}
          {mediaError && (
            <span style={{ fontSize: 11, color: "#f59e0b", flexShrink: 0 }} title="媒体预览加载失败，退回到附件卡片">
              预览失败
            </span>
          )}
          {docKind === "pdf" ? (
            // external-link: PDF opens standalone in a new tab.
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, color: "#0891b2" }} aria-hidden>
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
            </svg>
          ) : docKind ? (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, color: "#0891b2" }} aria-hidden>
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
            </svg>
          ) : (
            <span style={{ flexShrink: 0, color: "#0891b2", display: "inline-flex" }}>
              <IconDownload size={14} />
            </span>
          )}
        </button>
        {docKind && (
          <button
            type="button"
            onClick={handleDownload}
            title="下载"
            aria-label="下载"
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              flexShrink: 0, width: 30, borderRadius: 5, cursor: "pointer",
              background: "rgba(8,145,178,0.08)", border: "1px solid rgba(8,145,178,0.2)",
              color: "#0891b2",
            }}
          >
            <IconDownload size={14} />
          </button>
        )}
      </div>
      {contextMenu}
      {docPreviewModal}
    </>
  );
}

const menuItemStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "6px 10px",
  background: "none",
  border: "none",
  cursor: "pointer",
  textAlign: "left",
  borderRadius: 4,
  color: "var(--text)",
};

const docModalActionBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  background: "rgba(8,145,178,0.12)",
  border: "1px solid rgba(8,145,178,0.25)",
  color: "#0891b2",
  borderRadius: 5,
  padding: "6px 12px",
  cursor: "pointer",
  fontSize: 12,
};
