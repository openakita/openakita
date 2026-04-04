import { useRef, useEffect, useCallback } from "react";

export function ContextMenuInner({ ctxMenu, setCtxMenu, children }: {
  ctxMenu: { x: number; y: number; convId: string };
  setCtxMenu: (v: null) => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const focusIdxRef = useRef(-1);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    if (rect.right > vw) el.style.left = `${Math.max(4, ctxMenu.x - rect.width)}px`;
    if (rect.bottom > vh) el.style.top = `${Math.max(4, ctxMenu.y - rect.height)}px`;
  }, [ctxMenu.x, ctxMenu.y]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    const items = ref.current?.querySelectorAll<HTMLElement>(":scope > div");
    if (!items?.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const dir = e.key === "ArrowDown" ? 1 : -1;
      const next = Math.max(0, Math.min(focusIdxRef.current + dir, items.length - 1));
      focusIdxRef.current = next;
      items.forEach((it, i) => {
        it.style.background = i === next ? "rgba(37,99,235,0.08)" : "";
      });
    } else if (e.key === "Enter" && focusIdxRef.current >= 0) {
      e.preventDefault();
      items[focusIdxRef.current]?.click();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setCtxMenu(null);
    }
  }, [setCtxMenu]);

  return (
    <div
      ref={ref}
      tabIndex={-1}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={handleKeyDown}
      style={{
        position: "fixed",
        left: ctxMenu.x,
        top: ctxMenu.y,
        background: "var(--panel)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        boxShadow: "0 8px 24px rgba(0,0,0,0.22)",
        padding: "4px 0",
        minWidth: 140,
        fontSize: 13,
        zIndex: 10000,
        outline: "none",
      }}
    >
      {children}
    </div>
  );
}
