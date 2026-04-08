/**
 * 20 preset SVG avatars for org nodes.
 * Flat-style character busts — each role has a unique silhouette feature.
 */
import React from "react";

let _apiBase = "";
export function setAvatarApiBase(base: string) { _apiBase = base; }

function resolveAvatarUrl(raw: string): string {
  if (raw.startsWith("http")) return raw;
  if (raw.startsWith("/") && _apiBase) return _apiBase + raw;
  return raw;
}

export interface AvatarPreset {
  id: string;
  bg: string;
  label: string;
  /** Render the inner SVG paths (white on colored bg) */
  icon: (color?: string) => React.ReactElement;
}

/* ---- shared head+shoulders base ---- */
const Head = ({ cy = 14, r = 7, fill = "#fff" }: { cy?: number; r?: number; fill?: string }) => (
  <circle cx="20" cy={cy} r={r} fill={fill} />
);
const Shoulders = ({ fill = "#fff" }: { fill?: string }) => (
  <path d="M8 38 C8 30 14 26 20 26 C26 26 32 30 32 38" fill={fill} />
);

/* ---- individual icons ---- */

const CeoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <polygon points="20,26 17,34 20,31.5 23,34" fill="currentColor" opacity=".5" />
  </g>
);

const CtoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <rect x="13.5" y="12" width="5.5" height="3.5" rx="1" fill="currentColor" opacity=".45" />
    <rect x="21" y="12" width="5.5" height="3.5" rx="1" fill="currentColor" opacity=".45" />
    <line x1="19" y1="13.5" x2="21" y2="13.5" stroke="currentColor" strokeWidth="1" opacity=".45" />
  </g>
);

const CfoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <circle cx="20" cy="33" r="4.5" fill="currentColor" opacity=".3" />
    <text x="20" y="35.5" fontSize="7" fill="currentColor" opacity=".6" textAnchor="middle" fontWeight="bold">$</text>
  </g>
);

const CmoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <polygon points="20,27.5 21.8,31 25.5,31.5 23,34 23.5,37.5 20,35.5 16.5,37.5 17,34 14.5,31.5 18.2,31" fill="currentColor" opacity=".4" />
  </g>
);

const CpoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <circle cx="20" cy="33" r="5" fill="none" stroke="currentColor" strokeWidth="1.2" opacity=".4" />
    <circle cx="20" cy="33" r="2.5" fill="none" stroke="currentColor" strokeWidth="1" opacity=".35" />
    <circle cx="20" cy="33" r=".8" fill="currentColor" opacity=".5" />
  </g>
);

const ArchitectIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <polygon points="14,37 26,37 20,27" fill="none" stroke="currentColor" strokeWidth="1.2" opacity=".45" />
    <line x1="17" y1="32" x2="23" y2="32" stroke="currentColor" strokeWidth=".8" opacity=".35" />
  </g>
);

const DevMIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <text x="20" y="36" fontSize="9" fill="currentColor" opacity=".45" fontFamily="monospace" textAnchor="middle">&lt;/&gt;</text>
  </g>
);

const DevFIcon = (c = "#fff") => (
  <g>
    <path d="M13 14 Q13 7 20 7 Q27 7 27 14 L27 16 Q28 20 26 20 L14 20 Q12 20 13 16Z" fill={c} opacity=".55" />
    <Head fill={c} />
    <Shoulders fill={c} />
    <text x="20" y="36" fontSize="9" fill="currentColor" opacity=".45" fontFamily="monospace" textAnchor="middle">&lt;/&gt;</text>
  </g>
);

const DevopsIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <g transform="translate(20,33)" opacity=".45">
      <circle cx="0" cy="0" r="4" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="0" cy="0" r="1.8" fill="currentColor" />
      {[0, 60, 120, 180, 240, 300].map((a) => (
        <rect key={a} x="-.7" y="-5.5" width="1.4" height="2.8" rx=".3" fill="currentColor" transform={`rotate(${a})`} />
      ))}
    </g>
  </g>
);

const DesignerMIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <ellipse cx="20" cy="33" rx="5" ry="4" fill="currentColor" opacity=".3" />
    <circle cx="17.5" cy="31.5" r="1.2" fill="currentColor" opacity=".6" />
    <circle cx="21" cy="31" r="1" fill="currentColor" opacity=".5" />
    <circle cx="19" cy="35" r="1.1" fill="currentColor" opacity=".55" />
  </g>
);

const DesignerFIcon = (c = "#fff") => (
  <g>
    <path d="M13 14 Q13 7 20 7 Q27 7 27 14 L27 16 Q28 20 26 20 L14 20 Q12 20 13 16Z" fill={c} opacity=".55" />
    <Head fill={c} />
    <Shoulders fill={c} />
    <rect x="18.5" y="27" width="2.5" height="11" rx=".6" fill="currentColor" opacity=".45" />
    <polygon points="18.5,38 21,38 19.75,40" fill="currentColor" opacity=".4" />
  </g>
);

const PmIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <rect x="14.5" y="28" width="11" height="9" rx="1.2" fill="currentColor" opacity=".3" />
    <rect x="16.5" y="26.5" width="7" height="2.5" rx="1" fill="currentColor" opacity=".45" />
    <line x1="16.5" y1="32" x2="23.5" y2="32" stroke="currentColor" strokeWidth=".8" opacity=".5" />
    <line x1="16.5" y1="34.5" x2="23.5" y2="34.5" stroke="currentColor" strokeWidth=".8" opacity=".5" />
  </g>
);

const AnalystIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <rect x="14" y="33" width="3.5" height="5" rx=".4" fill="currentColor" opacity=".4" />
    <rect x="18.2" y="30" width="3.5" height="8" rx=".4" fill="currentColor" opacity=".45" />
    <rect x="22.5" y="32" width="3.5" height="6" rx=".4" fill="currentColor" opacity=".35" />
  </g>
);

const MarketerIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <polyline points="12,37 17,31 21,33 28,26" fill="none" stroke="currentColor" strokeWidth="2" opacity=".4" strokeLinecap="round" strokeLinejoin="round" />
    <polygon points="26,24 29.5,26 27,29" fill="currentColor" opacity=".45" />
  </g>
);

const WriterIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <line x1="16" y1="37" x2="24" y2="27" stroke="currentColor" strokeWidth="2" opacity=".4" strokeLinecap="round" />
    <circle cx="24" cy="27" r="1.2" fill="currentColor" opacity=".45" />
  </g>
);

const HrIcon = (c = "#fff") => (
  <g>
    <circle cx="15" cy="13" r="6" fill={c} />
    <path d="M6 37 C6 29 10 24 15 24 C20 24 24 29 24 37" fill={c} />
    <circle cx="26" cy="15" r="5" fill={c} opacity=".6" />
    <path d="M18 38 C18 31 21 27 26 27 C31 27 34 31 34 38" fill={c} opacity=".6" />
  </g>
);

const LegalIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <line x1="20" y1="26" x2="20" y2="37" stroke="currentColor" strokeWidth="1.2" opacity=".45" />
    <line x1="13" y1="29" x2="27" y2="29" stroke="currentColor" strokeWidth="1.2" opacity=".45" />
    <path d="M13 29 L11 33.5 L15 33.5 Z" fill="currentColor" opacity=".4" />
    <path d="M27 29 L25 33.5 L29 33.5 Z" fill="currentColor" opacity=".4" />
  </g>
);

const SupportIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <path d="M12 15 Q12 7 20 7 Q28 7 28 15" fill="none" stroke="currentColor" strokeWidth="2" opacity=".4" />
    <rect x="10" y="13" width="3.5" height="6" rx="1.2" fill="currentColor" opacity=".45" />
    <rect x="26.5" y="13" width="3.5" height="6" rx="1.2" fill="currentColor" opacity=".45" />
    <path d="M11 19 Q11 23 15 23" fill="none" stroke="currentColor" strokeWidth="1.2" opacity=".4" />
  </g>
);

const ResearcherIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <circle cx="19" cy="33" r="4" fill="none" stroke="currentColor" strokeWidth="1.5" opacity=".45" />
    <line x1="22" y1="36" x2="26" y2="39" stroke="currentColor" strokeWidth="2" opacity=".45" strokeLinecap="round" />
  </g>
);

const MediaIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    <rect x="16.5" y="27" width="7" height="10" rx="1.5" fill="currentColor" opacity=".35" />
    <circle cx="20" cy="35.5" r=".8" fill="currentColor" opacity=".55" />
  </g>
);

/* ---- preset registry ---- */

export const AVATAR_PRESETS: AvatarPreset[] = [
  { id: "ceo",         bg: "#1a365d", label: "CEO / 总裁",        icon: CeoIcon },
  { id: "cto",         bg: "#2b6cb0", label: "CTO / 技术总监",    icon: CtoIcon },
  { id: "cfo",         bg: "#2f855a", label: "CFO / 财务总监",    icon: CfoIcon },
  { id: "cmo",         bg: "#dd6b20", label: "CMO / 市场总监",    icon: CmoIcon },
  { id: "cpo",         bg: "#6b46c1", label: "CPO / 产品总监",    icon: CpoIcon },
  { id: "architect",   bg: "#2c5282", label: "架构师",            icon: ArchitectIcon },
  { id: "dev-m",       bg: "#3182ce", label: "开发工程师 (男)",    icon: DevMIcon },
  { id: "dev-f",       bg: "#00838f", label: "开发工程师 (女)",    icon: DevFIcon },
  { id: "devops",      bg: "#4a5568", label: "DevOps 工程师",     icon: DevopsIcon },
  { id: "designer-m",  bg: "#d53f8c", label: "设计师 (男)",       icon: DesignerMIcon },
  { id: "designer-f",  bg: "#b83280", label: "设计师 (女)",       icon: DesignerFIcon },
  { id: "pm",          bg: "#805ad5", label: "产品 / 项目经理",    icon: PmIcon },
  { id: "analyst",     bg: "#3182ce", label: "数据分析师",         icon: AnalystIcon },
  { id: "marketer",    bg: "#e53e3e", label: "市场营销",           icon: MarketerIcon },
  { id: "writer",      bg: "#744210", label: "文案 / 写手",       icon: WriterIcon },
  { id: "hr",          bg: "#c05621", label: "人力资源",           icon: HrIcon },
  { id: "legal",       bg: "#718096", label: "法务顾问",           icon: LegalIcon },
  { id: "support",     bg: "#319795", label: "客服支持",           icon: SupportIcon },
  { id: "researcher",  bg: "#276749", label: "研究员",            icon: ResearcherIcon },
  { id: "media",       bg: "#e53e3e", label: "社媒运营",           icon: MediaIcon },
];

export const AVATAR_MAP: Record<string, AvatarPreset> = {};
for (const a of AVATAR_PRESETS) AVATAR_MAP[a.id] = a;

/* ---- React component ---- */

interface OrgAvatarProps {
  avatarId: string | null | undefined;
  size?: number;
  /** Status dot color; null = no dot */
  statusColor?: string | null;
  statusGlow?: boolean;
  /** Tooltip text for the status dot */
  statusTitle?: string;
  onClick?: () => void;
  style?: React.CSSProperties;
}

/** Check if an avatar value is a custom URL (uploaded image) vs. a preset ID */
export function isCustomAvatar(avatarId: string | null | undefined): boolean {
  if (!avatarId) return false;
  return avatarId.startsWith("/") || avatarId.startsWith("http");
}

export function OrgAvatar({
  avatarId,
  size = 32,
  statusColor = null,
  statusGlow = false,
  statusTitle,
  onClick,
  style,
}: OrgAvatarProps) {
  const isCustom = isCustomAvatar(avatarId);
  const preset = !isCustom && avatarId ? AVATAR_MAP[avatarId] : undefined;
  const bg = preset?.bg ?? "#718096";

  const radius = Math.max(size * 0.22, 4);
  const containerStyle: React.CSSProperties = {
    width: size,
    height: size,
    boxSizing: "border-box",
    borderRadius: radius,
    background: isCustom ? "transparent" : bg,
    overflow: "hidden",
    boxShadow: statusGlow && statusColor
      ? `0 0 8px ${statusColor}`
      : "0 1px 3px rgba(0,0,0,0.18)",
    transition: "box-shadow .2s, transform .15s",
    ...style,
  };

  const wrapperStyle: React.CSSProperties = {
    position: "relative",
    width: size,
    height: size,
    flexShrink: 0,
    cursor: onClick ? "pointer" : undefined,
  };

  return (
    <div onClick={onClick} style={wrapperStyle}>
      <div style={containerStyle}>
        {isCustom ? (
          <img
            src={resolveAvatarUrl(avatarId!)}
            alt="avatar"
            style={{
              width: "100%",
              height: "100%",
              borderRadius: radius,
              objectFit: "cover",
              display: "block",
            }}
          />
        ) : (
          <svg
            viewBox="0 0 40 40"
            width="100%"
            height="100%"
            style={{ display: "block", color: bg }}
          >
            {preset ? preset.icon("#fff") : (
              <g>
                <Head fill="#fff" />
                <Shoulders fill="#fff" />
              </g>
            )}
          </svg>
        )}
      </div>
      {statusColor && (
        <div
          title={statusTitle}
          style={{
            position: "absolute",
            bottom: -1,
            right: -1,
            width: Math.max(size * 0.25, 7),
            height: Math.max(size * 0.25, 7),
            borderRadius: "50%",
            background: statusColor,
            border: "1.5px solid var(--card-bg, #fff)",
            boxShadow: statusGlow ? `0 0 4px ${statusColor}` : undefined,
          }}
        />
      )}
    </div>
  );
}
