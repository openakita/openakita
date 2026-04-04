import { type ReactNode, type CSSProperties } from "react";

interface PanelShellProps {
  open: boolean;
  onClose: () => void;
  width?: number | string;
  maxWidth?: number | string;
  side?: "left" | "right";
  isMobile: boolean;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

/**
 * Unified container for sidepanels in the Org Editor.
 *
 * - Mobile: absolute overlay + backdrop + slide animation
 * - Desktop: inline flex item + border + subtle animation
 *
 * ESC-to-close is handled at the page level (single global handler)
 * to avoid conflicts when multiple PanelShells are mounted.
 */
export function PanelShell({
  open,
  onClose,
  width = 300,
  maxWidth,
  side = "right",
  isMobile,
  children,
  className,
  style,
}: PanelShellProps) {
  if (!open) return null;

  if (isMobile) {
    return (
      <>
        <div className="ps-overlay" onClick={onClose} />
        <div
          className={`ps-panel ps-mobile ps-${side} ${className || ""}`}
          style={{ maxWidth: maxWidth ?? 360, ...style }}
        >
          {children}
        </div>
      </>
    );
  }

  return (
    <div
      className={`ps-panel ps-desktop ps-${side} ${className || ""}`}
      style={{ width, maxWidth: maxWidth ?? width, ...style }}
    >
      {children}
    </div>
  );
}
