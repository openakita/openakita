// ─── SVG Icon Components ───
// Centralized icon library. All icons are inline SVGs with consistent API.
// Style: Lucide/Feather-inspired, 24x24 viewBox, stroke-based, currentColor.

import React from "react";

type IconProps = {
  size?: number;
  className?: string;
  style?: React.CSSProperties;
  color?: string;
  strokeWidth?: number;
  onClick?: React.MouseEventHandler<SVGSVGElement>;
};

const defaults = { size: 18, strokeWidth: 2 };

function svg(
  props: IconProps,
  children: React.ReactNode,
  viewBox = "0 0 24 24",
) {
  const { size = defaults.size, className, style, color, strokeWidth = defaults.strokeWidth, onClick } = props;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox={viewBox}
      fill="none"
      stroke={color || "currentColor"}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      onClick={onClick}
    >
      {children}
    </svg>
  );
}

// ─── Navigation / Sidebar ───

export function IconChat(p: IconProps = {}) {
  return svg(p, <>
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </>);
}

export function IconMessageCircle(p: IconProps = {}) {
  return svg(p, <>
    <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z" />
  </>);
}

export function IconSkills(p: IconProps = {}) {
  return svg(p, <>
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
  </>);
}

export function IconStatus(p: IconProps = {}) {
  return svg(p, <>
    <line x1="18" y1="20" x2="18" y2="10" />
    <line x1="12" y1="20" x2="12" y2="4" />
    <line x1="6" y1="20" x2="6" y2="14" />
  </>);
}

export function IconConfig(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </>);
}

export function IconIM(p: IconProps = {}) {
  return svg(p, <>
    <path d="M16 3h5v5" />
    <line x1="21" y1="3" x2="14" y2="10" />
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h6" />
  </>);
}

// ─── Actions ───

export function IconSend(p: IconProps = {}) {
  return svg(p, <>
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </>);
}

export function IconRefresh(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="23 4 23 10 17 10" />
    <polyline points="1 20 1 14 7 14" />
    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
  </>);
}

export function IconPlus(p: IconProps = {}) {
  return svg(p, <>
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </>);
}

export function IconStop(p: IconProps = {}) {
  return svg(p, <>
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
  </>);
}

// ─── Chat Input ───

export function IconPaperclip(p: IconProps = {}) {
  return svg(p, <>
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
  </>);
}

export function IconMic(p: IconProps = {}) {
  return svg(p, <>
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </>);
}

export function IconStopCircle(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <rect x="9" y="9" width="6" height="6" />
  </>);
}

export function IconPlan(p: IconProps = {}) {
  return svg(p, <>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="16" y1="13" x2="8" y2="13" />
    <line x1="16" y1="17" x2="8" y2="17" />
    <polyline points="10 9 9 9 8 9" />
  </>);
}

export function IconImage(p: IconProps = {}) {
  return svg(p, <>
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
    <circle cx="8.5" cy="8.5" r="1.5" />
    <polyline points="21 15 16 10 5 21" />
  </>);
}

// ─── Status / Indicators ───

export function IconCheck(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="20 6 9 17 4 12" />
  </>);
}

export function IconCheckCircle(p: IconProps = {}) {
  return svg(p, <>
    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
    <polyline points="22 4 12 14.01 9 11.01" />
  </>);
}

export function IconX(p: IconProps = {}) {
  return svg(p, <>
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </>);
}

export function IconXCircle(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <line x1="15" y1="9" x2="9" y2="15" />
    <line x1="9" y1="9" x2="15" y2="15" />
  </>);
}

export function IconLoader(p: IconProps = {}) {
  return svg(p, <>
    <line x1="12" y1="2" x2="12" y2="6" />
    <line x1="12" y1="18" x2="12" y2="22" />
    <line x1="4.93" y1="4.93" x2="7.76" y2="7.76" />
    <line x1="16.24" y1="16.24" x2="19.07" y2="19.07" />
    <line x1="2" y1="12" x2="6" y2="12" />
    <line x1="18" y1="12" x2="22" y2="12" />
    <line x1="4.93" y1="19.07" x2="7.76" y2="16.24" />
    <line x1="16.24" y1="7.76" x2="19.07" y2="4.93" />
  </>);
}

export function IconCircle(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
  </>);
}

export function IconCircleDot(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />
  </>);
}

// ─── Chevrons / Arrows ───

export function IconChevronDown(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="6 9 12 15 18 9" />
  </>);
}

export function IconChevronRight(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="9 18 15 12 9 6" />
  </>);
}

export function IconChevronUp(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="18 15 12 9 6 15" />
  </>);
}

// ─── Tool Call / Plan Status ───

export function IconPlay(p: IconProps = {}) {
  return svg(p, <>
    <polygon points="5 3 19 12 5 21 5 3" />
  </>);
}

export function IconMinus(p: IconProps = {}) {
  return svg(p, <>
    <line x1="5" y1="12" x2="19" y2="12" />
  </>);
}

// ─── Slash Commands ───

export function IconModel(p: IconProps = {}) {
  return svg(p, <>
    <path d="M12 2L2 7l10 5 10-5-10-5z" />
    <path d="M2 17l10 5 10-5" />
    <path d="M2 12l10 5 10-5" />
  </>);
}

export function IconClipboard(p: IconProps = {}) {
  return svg(p, <>
    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
  </>);
}

export function IconTrash(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </>);
}

export function IconMask(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <path d="M8 14s1.5 2 4 2 4-2 4-2" />
    <line x1="9" y1="9" x2="9.01" y2="9" />
    <line x1="15" y1="9" x2="15.01" y2="9" />
  </>);
}

export function IconUser(p: IconProps = {}) {
  return svg(p, <>
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </>);
}

export function IconUsers(p: IconProps = {}) {
  return svg(p, <>
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </>);
}

export function IconBot(p: IconProps = {}) {
  return svg(p, <>
    <rect x="3" y="11" width="18" height="10" rx="2" />
    <circle cx="12" cy="5" r="2" />
    <path d="M12 7v4" />
    <line x1="8" y1="16" x2="8" y2="16" />
    <line x1="16" y1="16" x2="16" y2="16" />
  </>);
}

export function IconBotOff(p: IconProps = {}) {
  return svg(p, <>
    <rect x="3" y="11" width="18" height="10" rx="2" />
    <circle cx="12" cy="5" r="2" />
    <path d="M12 7v4" />
    <line x1="8" y1="16" x2="8" y2="16" />
    <line x1="16" y1="16" x2="16" y2="16" />
    <line x1="2" y1="2" x2="22" y2="22" strokeWidth="2" />
  </>);
}

export function IconHelp(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </>);
}

// ─── Skill Manager ───

export function IconPackage(p: IconProps = {}) {
  return svg(p, <>
    <line x1="16.5" y1="9.4" x2="7.5" y2="4.21" />
    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
    <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
    <line x1="12" y1="22.08" x2="12" y2="12" />
  </>);
}

export function IconStar(p: IconProps = {}) {
  return svg(p, <>
    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
  </>);
}

export function IconZap(p: IconProps = {}) {
  return svg(p, <>
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
  </>);
}

export function IconPin(p: IconProps = {}) {
  return svg(p, <>
    <line x1="12" y1="17" x2="12" y2="22" />
    <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
  </>);
}

export function IconGear(p: IconProps = {}) {
  return IconConfig(p);
}

// ─── Misc ───

export function IconFile(p: IconProps = {}) {
  return svg(p, <>
    <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
    <polyline points="13 2 13 9 20 9" />
  </>);
}

export function IconVolume(p: IconProps = {}) {
  return svg(p, <>
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
    <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
  </>);
}

export function IconDownload(p: IconProps = {}) {
  return svg(p, <>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </>);
}

export function IconUpload(p: IconProps = {}) {
  return svg(p, <>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </>);
}

export function IconFolderOpen(p: IconProps = {}) {
  return svg(p, <>
    <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
    <path d="M2 10h20" />
  </>);
}

export function IconSearch(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="11" cy="11" r="8" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </>);
}

export function IconGlobe(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </>);
}

export function IconMenu(p: IconProps = {}) {
  return svg(p, <>
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </>);
}

// ─── Filled status dots (used for health indicators) ───

export function DotGreen(p: { size?: number }) {
  const s = p.size ?? 8;
  return (
    <span
      style={{
        display: "inline-block",
        width: s,
        height: s,
        borderRadius: "50%",
        background: "#22c55e",
        flexShrink: 0,
      }}
    />
  );
}

export function DotRed(p: { size?: number }) {
  const s = p.size ?? 8;
  return (
    <span
      style={{
        display: "inline-block",
        width: s,
        height: s,
        borderRadius: "50%",
        background: "#ef4444",
        flexShrink: 0,
      }}
    />
  );
}

export function DotGray(p: { size?: number }) {
  const s = p.size ?? 8;
  return (
    <span
      style={{
        display: "inline-block",
        width: s,
        height: s,
        borderRadius: "50%",
        background: "#9ca3af",
        flexShrink: 0,
      }}
    />
  );
}

export function DotYellow(p: { size?: number }) {
  const s = p.size ?? 8;
  return (
    <span
      style={{
        display: "inline-block",
        width: s,
        height: s,
        borderRadius: "50%",
        background: "#eab308",
        flexShrink: 0,
      }}
    />
  );
}

export function DotBlueProcessing(p: { size?: number }) {
  const s = p.size ?? 8;
  return (
    <span className="dotProcessing" style={{ width: s, height: s }} />
  );
}

export function IconLink(p: IconProps = {}) {
  return svg(p, <>
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </>);
}

export function IconPower(p: IconProps = {}) {
  return svg(p, <>
    <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
    <line x1="12" y1="2" x2="12" y2="12" />
  </>);
}

export function IconEdit(p: IconProps = {}) {
  return svg(p, <>
    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
  </>);
}

export function IconEye(p: IconProps = {}) {
  return svg(p, <>
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
    <circle cx="12" cy="12" r="3" />
  </>);
}

export function IconEyeOff(p: IconProps = {}) {
  return svg(p, <>
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </>);
}

export function IconInfo(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </>);
}

export function IconBook(p: IconProps = {}) {
  return svg(p, <>
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </>);
}

export function IconMoon(p: IconProps = {}) {
  return svg(p, <>
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
  </>);
}

export function IconSun(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="5" />
    <line x1="12" y1="1" x2="12" y2="3" />
    <line x1="12" y1="21" x2="12" y2="23" />
    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
    <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
    <line x1="1" y1="12" x2="3" y2="12" />
    <line x1="21" y1="12" x2="23" y2="12" />
    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
    <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
  </>);
}

export function IconLaptop(p: IconProps = {}) {
  return svg(p, <>
    <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
    <line x1="8" y1="21" x2="16" y2="21" />
    <line x1="12" y1="17" x2="12" y2="21" />
  </>);
}

export function IconCalendar(p: IconProps = {}) {
  return svg(p, <>
    <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
    <line x1="16" y1="2" x2="16" y2="6" />
    <line x1="8" y1="2" x2="8" y2="6" />
    <line x1="3" y1="10" x2="21" y2="10" />
  </>);
}

export function IconClock(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <polyline points="12 6 12 12 16 14" />
  </>);
}

export function IconPlug(p: IconProps = {}) {
  return svg(p, <>
    <path d="M12 22v-5" />
    <path d="M9 8V1h6v7" />
    <path d="M7 8h10a2 2 0 0 1 2 2v2a5 5 0 0 1-5 5h-4a5 5 0 0 1-5-5v-2a2 2 0 0 1 2-2z" />
  </>);
}

// ── IM Platform Logos (from AstrBot resource files) ──

import telegramLogoUrl from "./assets/platform_logos/telegram.svg";
import discordLogoUrl from "./assets/platform_logos/discord.svg";
import dingtalkLogoUrl from "./assets/platform_logos/dingtalk.svg";
import slackLogoUrl from "./assets/platform_logos/slack.svg";
import larkLogoUrl from "./assets/platform_logos/lark.png";
import wecomLogoUrl from "./assets/platform_logos/wecom.png";
import qqLogoUrl from "./assets/platform_logos/qq.png";
import onebotLogoUrl from "./assets/platform_logos/onebot.png";
import lineLogoUrl from "./assets/platform_logos/line.png";
import wechatLogoUrl from "./assets/platform_logos/wechat.png";

type LogoProps = { size?: number };

function PlatformLogo({ src, alt, size = 20 }: { src: string; alt: string; size?: number }) {
  return <img src={src} alt={alt} width={size} height={size} style={{ borderRadius: 4, objectFit: "contain", display: "block" }} />;
}

export function LogoTelegram({ size = 20 }: LogoProps) {
  return <PlatformLogo src={telegramLogoUrl} alt="Telegram" size={size} />;
}

export function LogoFeishu({ size = 20 }: LogoProps) {
  return <PlatformLogo src={larkLogoUrl} alt="Feishu" size={size} />;
}

export function LogoWework({ size = 20 }: LogoProps) {
  return <PlatformLogo src={wecomLogoUrl} alt="WeCom" size={size} />;
}

export function LogoDingtalk({ size = 20 }: LogoProps) {
  return <PlatformLogo src={dingtalkLogoUrl} alt="DingTalk" size={size} />;
}

export function LogoQQ({ size = 20 }: LogoProps) {
  return <PlatformLogo src={qqLogoUrl} alt="QQ" size={size} />;
}

export function LogoDiscord({ size = 20 }: LogoProps) {
  return <PlatformLogo src={discordLogoUrl} alt="Discord" size={size} />;
}

export function LogoSlack({ size = 20 }: LogoProps) {
  return <PlatformLogo src={slackLogoUrl} alt="Slack" size={size} />;
}

export function LogoOneBot({ size = 20 }: LogoProps) {
  return <PlatformLogo src={onebotLogoUrl} alt="OneBot" size={size} />;
}

export function LogoLine({ size = 20 }: LogoProps) {
  return <PlatformLogo src={lineLogoUrl} alt="LINE" size={size} />;
}

export function LogoWechat({ size = 20 }: LogoProps) {
  return <PlatformLogo src={wechatLogoUrl} alt="WeChat" size={size} />;
}

export const IM_LOGO_MAP: Record<string, (p: { size?: number }) => React.JSX.Element> = {
  telegram: LogoTelegram,
  feishu: LogoFeishu,
  lark: LogoFeishu,
  wework: LogoWework,
  wework_ws: LogoWework,
  dingtalk: LogoDingtalk,
  qq: LogoQQ,
  qqbot: LogoQQ,
  onebot: LogoOneBot,
  onebot_reverse: LogoOneBot,
  discord: LogoDiscord,
  slack: LogoSlack,
  line: LogoLine,
  wechat: LogoWechat,
};

export function IconBug(p: IconProps = {}) {
  return svg(p, <>
    <path d="M8 2l1.88 1.88M14.12 3.88L16 2" />
    <path d="M9 7.13v-1a3.003 3.003 0 116 0v1" />
    <path d="M12 20c-3.3 0-6-2.7-6-6v-3a6 6 0 0112 0v3c0 3.3-2.7 6-6 6z" />
    <path d="M12 20v-9" />
    <path d="M6.53 9C4.6 8.8 3 7.1 3 5" />
    <path d="M6 13H2" />
    <path d="M3 21c0-2.1 1.7-3.9 3.8-4" />
    <path d="M20.97 5c0 2.1-1.6 3.8-3.5 4" />
    <path d="M22 13h-4" />
    <path d="M17.2 17c2.1.1 3.8 1.9 3.8 4" />
  </>);
}

export function IconGitHub(p: IconProps = {}) {
  const { size = defaults.size, className, style, color } = p;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={color || "currentColor"}
      className={className}
      style={style}
    >
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}

export function IconGitee(p: IconProps = {}) {
  const { size = defaults.size, className, style, color } = p;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={color || "currentColor"}
      className={className}
      style={style}
    >
      <path d="M11.984 0A12 12 0 000 12a12 12 0 0012 12 12 12 0 0012-12A12 12 0 0012 0h-.016zm6.09 5.333c.328 0 .593.266.592.593v1.482a.594.594 0 01-.593.592H12.26c-.982 0-1.778.796-1.778 1.778v1.185h7.59a.594.594 0 01.592.593v1.482a.593.593 0 01-.593.592h-7.59v1.778c0 .982.797 1.778 1.779 1.778h5.813c.328 0 .593.266.593.593v1.482a.594.594 0 01-.593.593H12.26a4.451 4.451 0 01-4.444-4.444V9.778a4.451 4.451 0 014.444-4.444h5.813z" />
    </svg>
  );
}

export function IconBrain(p: IconProps = {}) {
  return svg(p, <>
    <path d="M9.5 2A5.5 5.5 0 0 0 5 5.5C5 4 3.5 3 2 3c0 2 1 3.5 2.5 4C3 7 2 8 2 9.5c0 1.5 1 3 2.5 3.5-.5.5-1 1.5-1 2.5C3.5 17.5 5 19 7 19c0 1.5 1 3 3 3h4c2 0 3-1.5 3-3 2 0 3.5-1.5 3.5-3.5 0-1-.5-2-1-2.5C21 12.5 22 11 22 9.5c0-1.5-1-2.5-2.5-2.5 1.5-.5 2.5-2 2.5-4-1.5 0-3 1-3 2.5A5.5 5.5 0 0 0 14.5 2h-5z" />
    <path d="M12 2v20" />
  </>);
}

// ─── File Type Icons (filled style, for artifact cards) ───

function fileSvg(
  p: IconProps,
  badgeColor: string,
  label: string,
) {
  const { size = 20, className, style } = p;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 32 32" fill="none" className={className} style={style}>
      <path d="M7 2h12l8 8v18a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#e8ecef" stroke="#bcc3cb" strokeWidth="1" />
      <path d="M19 2l8 8h-6a2 2 0 0 1-2-2V2z" fill="#d1d5db" />
      <rect x="3" y="17" width="22" height="10" rx="2" fill={badgeColor} />
      <text x="14" y="24.5" textAnchor="middle" fill="#fff" fontSize="7" fontWeight="700" fontFamily="system-ui,sans-serif">{label}</text>
    </svg>
  );
}

export function IconFileWord(p: IconProps = {}) { return fileSvg(p, "#2b579a", "DOC"); }
export function IconFileExcel(p: IconProps = {}) { return fileSvg(p, "#217346", "XLS"); }
export function IconFilePPT(p: IconProps = {}) { return fileSvg(p, "#d24726", "PPT"); }
export function IconFilePDF(p: IconProps = {}) { return fileSvg(p, "#e5252a", "PDF"); }
export function IconFileZip(p: IconProps = {}) { return fileSvg(p, "#f59e0b", "ZIP"); }
export function IconFileCode(p: IconProps = {}) { return fileSvg(p, "#6366f1", "CODE"); }
export function IconFileText(p: IconProps = {}) { return fileSvg(p, "#64748b", "TXT"); }
export function IconFileCSV(p: IconProps = {}) { return fileSvg(p, "#059669", "CSV"); }
export function IconFileImage(p: IconProps = {}) { return fileSvg(p, "#8b5cf6", "IMG"); }
export function IconFileAudio(p: IconProps = {}) { return fileSvg(p, "#ec4899", "MP3"); }
export function IconFileVideo(p: IconProps = {}) { return fileSvg(p, "#f43f5e", "MP4"); }
export function IconFileGeneric(p: IconProps = {}) { return fileSvg(p, "#94a3b8", "FILE"); }

const _EXT_ICON_MAP: Record<string, (p: IconProps) => React.JSX.Element> = {
  doc: IconFileWord, docx: IconFileWord, odt: IconFileWord, rtf: IconFileWord,
  xls: IconFileExcel, xlsx: IconFileExcel, ods: IconFileExcel,
  ppt: IconFilePPT, pptx: IconFilePPT, odp: IconFilePPT,
  pdf: IconFilePDF,
  zip: IconFileZip, rar: IconFileZip, "7z": IconFileZip, tar: IconFileZip, gz: IconFileZip,
  js: IconFileCode, ts: IconFileCode, tsx: IconFileCode, jsx: IconFileCode,
  py: IconFileCode, java: IconFileCode, cpp: IconFileCode, c: IconFileCode,
  rs: IconFileCode, go: IconFileCode, rb: IconFileCode, php: IconFileCode,
  html: IconFileCode, css: IconFileCode, json: IconFileCode, xml: IconFileCode,
  yaml: IconFileCode, yml: IconFileCode, toml: IconFileCode, sql: IconFileCode,
  sh: IconFileCode, bat: IconFileCode, md: IconFileText,
  txt: IconFileText, log: IconFileText, ini: IconFileText, cfg: IconFileText,
  csv: IconFileCSV, tsv: IconFileCSV,
  png: IconFileImage, jpg: IconFileImage, jpeg: IconFileImage, gif: IconFileImage,
  svg: IconFileImage, webp: IconFileImage, bmp: IconFileImage, ico: IconFileImage,
  mp3: IconFileAudio, wav: IconFileAudio, ogg: IconFileAudio, flac: IconFileAudio, aac: IconFileAudio,
  mp4: IconFileVideo, avi: IconFileVideo, mkv: IconFileVideo, mov: IconFileVideo, webm: IconFileVideo,
};

export function getFileTypeIcon(filename: string): (p: IconProps) => React.JSX.Element {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  return _EXT_ICON_MAP[ext] || IconFileGeneric;
}

// ─── Store Icons ───

export function IconStorefront(p: IconProps = {}) {
  return svg(p, <>
    <path d="M3 9l1.5-5h15L21 9" />
    <path d="M3 9h18v1a3 3 0 0 1-3 3h0a3 3 0 0 1-3-3h0a3 3 0 0 1-3 3h0a3 3 0 0 1-3-3h0a3 3 0 0 1-3 3h0a3 3 0 0 1-3-3V9z" />
    <path d="M5 13v8h14v-8" />
    <path d="M10 21v-5a2 2 0 0 1 2-2h0a2 2 0 0 1 2 2v5" />
  </>);
}

export function IconPuzzle(p: IconProps = {}) {
  return svg(p, <>
    <path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.501 2.501 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.61a2.404 2.404 0 0 1-1.705.707 2.402 2.402 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.402 2.402 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.315 8.685a.98.98 0 0 1 .837-.276c.47.07.802.48.968.925a2.501 2.501 0 1 0 3.214-3.214c-.446-.166-.855-.497-.925-.968a.979.979 0 0 1 .276-.837l1.61-1.61a2.404 2.404 0 0 1 1.705-.707c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02z" />
  </>);
}

export function IconFingerprint(p: IconProps = {}) {
  return svg(p, <>
    <path d="M2 12C2 6.5 6.5 2 12 2a10 10 0 0 1 8 4" />
    <path d="M5 19.5C5.5 18 6 15 6 12c0-.7.12-1.37.34-2" />
    <path d="M17.29 21.02c.12-.6.43-2.3.5-3.02" />
    <path d="M12 10a2 2 0 0 0-2 2c0 1.02-.1 2.51-.26 4" />
    <path d="M8.65 22c.21-.66.45-1.32.57-2" />
    <path d="M14 13.12c0 2.38 0 6.38-1 8.88" />
    <path d="M2 16h.01" />
    <path d="M21.8 16c.2-2 .131-5.354 0-6" />
    <path d="M9 6.8a6 6 0 0 1 9 5.2c0 .47 0 1.17-.02 2" />
  </>);
}

export function IconRadar(p: IconProps = {}) {
  return svg(p, <>
    <path d="M19.07 4.93A10 10 0 0 0 6.99 3.34" />
    <path d="M4 6h.01" />
    <path d="M2.29 9.62A10 10 0 1 0 21.31 8.35" />
    <path d="M16.24 7.76A6 6 0 1 0 8.23 16.67" />
    <path d="M12 18h.01" />
    <path d="M17.99 11.66A6 6 0 0 1 15.77 16.67" />
    <circle cx="12" cy="12" r="2" />
    <path d="m13.41 10.59 5.66-5.66" />
  </>);
}

export function IconSave(p: IconProps = {}) {
  return svg(p, <>
    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
    <polyline points="17 21 17 13 7 13 7 21" />
    <polyline points="7 3 7 8 15 8" />
  </>);
}

export function IconHeartPulse(p: IconProps = {}) {
  return svg(p, <>
    <path d="M19.5 12.572l-7.5 7.428l-7.5-7.428A5 5 0 0 1 12 6.006a5 5 0 0 1 7.5 6.572" />
    <path d="M5 12h2l1 3 2-6 1 3h2" />
  </>);
}

export function IconInbox(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
    <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
  </>);
}

export function IconMaximize2(p: IconProps = {}) {
  return svg(p, <>
    <polyline points="15 3 21 3 21 9" />
    <polyline points="9 21 3 21 3 15" />
    <line x1="21" y1="3" x2="14" y2="10" />
    <line x1="3" y1="21" x2="10" y2="14" />
  </>);
}

export function IconSnowflake(p: IconProps = {}) {
  return svg(p, <>
    <line x1="12" y1="2" x2="12" y2="22" />
    <path d="M20 16l-4-4 4-4" />
    <path d="M4 8l4 4-4 4" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <path d="M16 4l-4 4-4-4" />
    <path d="M8 20l4-4 4 4" />
  </>);
}

export function IconLayoutGrid(p: IconProps = {}) {
  return svg(p, <>
    <rect x="3" y="3" width="7" height="7" />
    <rect x="14" y="3" width="7" height="7" />
    <rect x="14" y="14" width="7" height="7" />
    <rect x="3" y="14" width="7" height="7" />
  </>);
}

export function IconBuilding(p: IconProps = {}) {
  return svg(p, <>
    <rect x="4" y="2" width="16" height="20" rx="2" ry="2" />
    <path d="M9 22v-4h6v4" />
    <path d="M8 6h.01" />
    <path d="M16 6h.01" />
    <path d="M12 6h.01" />
    <path d="M12 10h.01" />
    <path d="M12 14h.01" />
    <path d="M16 10h.01" />
    <path d="M16 14h.01" />
    <path d="M8 10h.01" />
    <path d="M8 14h.01" />
  </>);
}

export function IconSitemap(p: IconProps = {}) {
  return svg(p, <>
    <rect x="9" y="2" width="6" height="4" rx="1" />
    <rect x="2" y="14" width="6" height="4" rx="1" />
    <rect x="9" y="14" width="6" height="4" rx="1" />
    <rect x="16" y="14" width="6" height="4" rx="1" />
    <path d="M12 6v4" />
    <path d="M5 14v-2a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v2" />
  </>);
}

export function IconAlertCircle(p: IconProps = {}) {
  return svg(p, <>
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </>);
}
