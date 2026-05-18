import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { saveAttachment, showInFolder, openFileWithDefault, IS_TAURI } from "../platform";
import { getFileTypeIcon } from "../icons";

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

  const mediaKind = useMemo(() => detectMediaKind(file.filename), [file.filename]);
  const mediaUrl = useMemo(
    () => `${apiBaseUrl}/api/files?path=${encodeURIComponent(file.file_path)}`,
    [apiBaseUrl, file.file_path],
  );

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
    if (!previewOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPreviewOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [previewOpen]);

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
              src={mediaUrl}
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
              src={mediaUrl}
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
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </button>
          </div>
        </div>
        {contextMenu}
        {previewOpen && (
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
                src={mediaUrl}
                alt={file.filename}
                style={{ maxWidth: "95vw", maxHeight: "95vh", borderRadius: 4 }}
                onClick={e => e.stopPropagation()}
              />
            ) : (
              <video
                src={mediaUrl}
                controls
                autoPlay
                style={{ maxWidth: "95vw", maxHeight: "95vh", borderRadius: 4 }}
                onClick={e => e.stopPropagation()}
              />
            )}
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <button
        style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "6px 10px", borderRadius: 5,
          background: "rgba(8,145,178,0.08)",
          border: "1px solid rgba(8,145,178,0.2)",
          cursor: "pointer", width: "100%",
          textAlign: "left", fontSize: 12,
          transition: "background 0.15s",
        }}
        title={file.file_path}
        onMouseEnter={e => { e.currentTarget.style.background = "rgba(8,145,178,0.16)"; }}
        onMouseLeave={e => { e.currentTarget.style.background = "rgba(8,145,178,0.08)"; }}
        onClick={handleDownload}
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
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, color: "#0891b2" }}>
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
      </button>
      {contextMenu}
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
