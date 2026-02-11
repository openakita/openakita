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
};

const defaults = { size: 18, strokeWidth: 2 };

function svg(
  props: IconProps,
  children: React.ReactNode,
  viewBox = "0 0 24 24",
) {
  const { size = defaults.size, className, style, color, strokeWidth = defaults.strokeWidth } = props;
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

// ── IM Platform Logos (simplified brand marks) ──

export function LogoTelegram({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="11" fill="#2AABEE" />
      <path d="M5.5 11.5l11.2-4.3c.5-.2.9.1.8.7l-1.9 9c-.1.5-.5.6-.9.4l-2.7-2-1.3 1.3c-.1.1-.3.2-.5.2l.2-2.7 4.8-4.3c.2-.2 0-.3-.3-.1l-6 3.8-2.6-.8c-.5-.2-.5-.5.1-.7z" fill="#fff" />
    </svg>
  );
}

export function LogoFeishu({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="5" fill="#3370FF" />
      <path d="M7 8.5c0-.3.3-.5.5-.3l4.5 3.3 4.5-3.3c.2-.2.5 0 .5.3v6.5c0 .3-.2.5-.5.5H7.5c-.3 0-.5-.2-.5-.5V8.5z" fill="#fff" />
    </svg>
  );
}

export function LogoWework({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="5" fill="#07C160" />
      <path d="M12 4C7.6 4 4 7.1 4 11c0 2.2 1.2 4.1 3 5.4V20l3.2-1.8c.6.1 1.2.2 1.8.2 4.4 0 8-3.1 8-7S16.4 4 12 4z" fill="#fff" />
    </svg>
  );
}

export function LogoDingtalk({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="5" fill="#0089FF" />
      <path d="M17.2 10.2c-.4.2-1.1.5-2 .8l-.4.1.8 1.2.1.2H13l-.1.3v.8h2l-.3 1.1H13v2.3h-1.5v-2.3h-1.7l-.2-1.1h1.9V12.7H10l-.1-.2h2.5l-1.6-2.3c1.8-.5 3.2-1.3 4.2-2.2.5.5.9 1 1.2 1.5l1-.6z" fill="#fff" />
    </svg>
  );
}

export function LogoQQ({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="5" fill="#12B7F5" />
      <ellipse cx="12" cy="11" rx="5" ry="6" fill="#fff" />
      <ellipse cx="10.5" cy="10" rx="1" ry="1.5" fill="#333" />
      <ellipse cx="13.5" cy="10" rx="1" ry="1.5" fill="#333" />
      <path d="M9 15c0 0 1.5 2 3 2s3-2 3-2" stroke="#333" strokeWidth="0.8" fill="none" />
    </svg>
  );
}
