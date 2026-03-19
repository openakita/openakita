import { useRef, useCallback, useEffect, type ReactNode, type CSSProperties } from "react";
import { createPortal } from "react-dom";

export function ModalOverlay({
  children,
  onClose,
  className = "modalOverlay",
  style,
}: {
  children: ReactNode;
  onClose: () => void;
  className?: string;
  style?: CSSProperties;
}) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const pointerDownOnOverlay = useRef(false);

  useEffect(() => {
    const el = overlayRef.current;
    if (!el) return;

    const onPointerDown = (e: PointerEvent) => {
      pointerDownOnOverlay.current = e.target === el;
    };
    const onPointerUp = (e: PointerEvent) => {
      if (e.target === el && pointerDownOnOverlay.current) onClose();
      pointerDownOnOverlay.current = false;
    };

    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointerup", onPointerUp);
    return () => {
      el.removeEventListener("pointerdown", onPointerDown);
      el.removeEventListener("pointerup", onPointerUp);
    };
  }, [onClose]);

  return createPortal(
    <div ref={overlayRef} className={className} style={style}>
      {children}
    </div>,
    document.body,
  );
}
