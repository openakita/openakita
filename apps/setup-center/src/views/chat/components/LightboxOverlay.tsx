import { useState, useRef, useEffect, useCallback } from "react";
import { IconDownload, IconX } from "../../../icons";
import { logger } from "../../../platform";

export function LightboxOverlay({ lightbox, onClose, downloadFile: dlFile, showInFolder: showFolder, t }: {
  lightbox: { url: string; downloadUrl: string; name: string };
  onClose: () => void;
  downloadFile: (url: string, name: string) => Promise<string>;
  showInFolder: (path: string) => Promise<void>;
  t: (k: string, d?: string) => string;
}) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragging = useRef(false);
  const lastMouse = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "+" || e.key === "=") setZoom(z => Math.min(z * 1.25, 10));
      else if (e.key === "-") setZoom(z => Math.max(z / 1.25, 0.2));
      else if (e.key === "0") { setZoom(1); setPan({ x: 0, y: 0 }); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.stopPropagation();
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    setZoom(z => Math.max(0.2, Math.min(z * factor, 10)));
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (zoom <= 1) return;
    e.preventDefault();
    dragging.current = true;
    lastMouse.current = { x: e.clientX, y: e.clientY };
  }, [zoom]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragging.current) return;
    const dx = e.clientX - lastMouse.current.x;
    const dy = e.clientY - lastMouse.current.y;
    lastMouse.current = { x: e.clientX, y: e.clientY };
    setPan(p => ({ x: p.x + dx, y: p.y + dy }));
  }, []);

  const handleMouseUp = useCallback(() => { dragging.current = false; }, []);

  const lbBtnStyle: React.CSSProperties = {
    background: "rgba(255,255,255,0.25)", color: "#fff",
    border: "1px solid rgba(255,255,255,0.35)", borderRadius: 8,
    backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)",
    width: 40, height: 40,
    display: "flex", alignItems: "center", justifyContent: "center",
    cursor: "pointer", transition: "background 0.15s",
  };

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 99999,
        background: "rgba(0,0,0,0.85)", backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        cursor: zoom > 1 ? (dragging.current ? "grabbing" : "grab") : "zoom-out",
      }}
      onClick={() => { if (!dragging.current) onClose(); }}
      onWheel={handleWheel}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <img
        src={lightbox.url}
        alt={lightbox.name}
        draggable={false}
        style={{
          maxWidth: "90vw", maxHeight: "90vh",
          borderRadius: 8, objectFit: "contain",
          boxShadow: "0 8px 48px rgba(0,0,0,0.5)",
          cursor: zoom > 1 ? "inherit" : "default",
          transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
          transition: dragging.current ? "none" : "transform 0.15s ease",
        }}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={handleMouseDown}
      />
      <div style={{ position: "absolute", top: 16, right: 16, display: "flex", gap: 8 }}>
        {zoom !== 1 && (
          <button style={lbBtnStyle} title="Reset zoom (0)" onClick={(e) => { e.stopPropagation(); setZoom(1); setPan({ x: 0, y: 0 }); }}>
            <span style={{ fontSize: 13, fontWeight: 700 }}>{Math.round(zoom * 100)}%</span>
          </button>
        )}
        <button
          title={t("chat.downloadImage", "保存图片")}
          style={lbBtnStyle}
          onClick={async (e) => {
            e.stopPropagation();
            try {
              const saved = await dlFile(lightbox.downloadUrl, lightbox.name || `image-${Date.now()}.png`);
              await showFolder(saved);
            } catch (err) {
              logger.error("Chat", "图片下载失败", { error: String(err) });
            }
          }}
        >
          <IconDownload size={18} />
        </button>
        <button style={lbBtnStyle} onClick={(e) => { e.stopPropagation(); onClose(); }}>
          <IconX size={18} />
        </button>
      </div>
    </div>
  );
}
