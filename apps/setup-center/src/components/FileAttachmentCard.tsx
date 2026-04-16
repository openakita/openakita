import { useState, useRef, useEffect, useCallback } from "react";
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

interface FileAttachmentCardProps {
  file: FileAttachment;
  apiBaseUrl: string;
}

export function FileAttachmentCard({ file, apiBaseUrl }: FileAttachmentCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState({ x: 0, y: 0 });
  const menuRef = useRef<HTMLDivElement>(null);

  const handleDownload = useCallback(async () => {
    try {
      await saveAttachment({
        apiUrl: `${apiBaseUrl}/api/files?path=${encodeURIComponent(file.file_path)}`,
        filename: file.filename,
      });
    } catch (e) {
      console.error("File save failed:", e);
    }
  }, [apiBaseUrl, file]);

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

  const Icon = getFileTypeIcon(file.filename);

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
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, color: "#0891b2" }}>
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
      </button>
      {menuOpen && (
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
            style={{ display: "block", width: "100%", padding: "6px 10px", background: "none", border: "none", cursor: "pointer", textAlign: "left", borderRadius: 4, color: "var(--text)" }}
            onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
            onClick={() => { setMenuOpen(false); handleDownload(); }}
          >
            下载文件
          </button>
          {IS_TAURI && (
            <>
              <button
                style={{ display: "block", width: "100%", padding: "6px 10px", background: "none", border: "none", cursor: "pointer", textAlign: "left", borderRadius: 4, color: "var(--text)" }}
                onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
                onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
                onClick={() => { setMenuOpen(false); openFileWithDefault(file.file_path); }}
              >
                用默认应用打开
              </button>
              <button
                style={{ display: "block", width: "100%", padding: "6px 10px", background: "none", border: "none", cursor: "pointer", textAlign: "left", borderRadius: 4, color: "var(--text)" }}
                onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; }}
                onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
                onClick={() => { setMenuOpen(false); showInFolder(file.file_path); }}
              >
                在文件管理器中显示
              </button>
            </>
          )}
        </div>
      )}
    </>
  );
}
