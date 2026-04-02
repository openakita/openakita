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
    {/* tie */}
    <polygon points="20,26 17,34 20,32 23,34" fill="currentColor" opacity=".35" />
  </g>
);

const CtoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* glasses */}
    <rect x="14" y="12" width="5" height="3.5" rx="1" fill="currentColor" opacity=".3" />
    <rect x="21" y="12" width="5" height="3.5" rx="1" fill="currentColor" opacity=".3" />
    <line x1="19" y1="13.5" x2="21" y2="13.5" stroke="currentColor" strokeWidth=".8" opacity=".3" />
  </g>
);

const CfoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* briefcase */}
    <rect x="14" y="30" width="12" height="7" rx="1.5" fill="currentColor" opacity=".3" />
    <rect x="17" y="28.5" width="6" height="2.5" rx="1" fill="none" stroke="currentColor" strokeWidth=".8" opacity=".3" />
  </g>
);

const CmoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* megaphone */}
    <polygon points="28,20 35,16 35,24" fill="currentColor" opacity=".3" />
    <rect x="26" y="19" width="3" height="3" rx=".5" fill="currentColor" opacity=".25" />
  </g>
);

const CpoIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* target/bullseye */}
    <circle cx="30" cy="30" r="5" fill="none" stroke="currentColor" strokeWidth="1" opacity=".3" />
    <circle cx="30" cy="30" r="2" fill="currentColor" opacity=".3" />
  </g>
);

const ArchitectIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* blueprint/ruler */}
    <rect x="27" y="22" width="2" height="12" rx=".5" fill="currentColor" opacity=".3" transform="rotate(-20 28 28)" />
    <rect x="30" y="24" width="2" height="10" rx=".5" fill="currentColor" opacity=".2" transform="rotate(15 31 29)" />
  </g>
);

const DevMIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* code brackets */}
    <text x="10" y="36" fontSize="9" fill="currentColor" opacity=".3" fontFamily="monospace">&lt;/&gt;</text>
  </g>
);

const DevFIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    {/* longer hair */}
    <path d="M13 14 Q13 7 20 7 Q27 7 27 14 L27 16 Q28 20 26 20 L14 20 Q12 20 13 16Z" fill={c} opacity=".5" />
    <Head fill={c} />
    <Shoulders fill={c} />
    <text x="10" y="36" fontSize="9" fill="currentColor" opacity=".3" fontFamily="monospace">&lt;/&gt;</text>
  </g>
);

const DevopsIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* gear */}
    <g transform="translate(29,28)" opacity=".35">
      <circle cx="0" cy="0" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <circle cx="0" cy="0" r="1.5" fill="currentColor" />
      {[0, 60, 120, 180, 240, 300].map((a) => (
        <rect key={a} x="-.6" y="-5" width="1.2" height="2.5" rx=".3" fill="currentColor" transform={`rotate(${a})`} />
      ))}
    </g>
  </g>
);

const DesignerMIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* palette */}
    <circle cx="30" cy="30" r="5" fill="currentColor" opacity=".25" />
    <circle cx="28" cy="28" r="1" fill="currentColor" opacity=".5" />
    <circle cx="31" cy="29" r=".8" fill="currentColor" opacity=".4" />
    <circle cx="30" cy="32" r=".9" fill="currentColor" opacity=".45" />
  </g>
);

const DesignerFIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <path d="M13 14 Q13 7 20 7 Q27 7 27 14 L27 16 Q28 20 26 20 L14 20 Q12 20 13 16Z" fill={c} opacity=".5" />
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* pencil */}
    <rect x="28" y="20" width="2" height="12" rx=".5" fill="currentColor" opacity=".3" transform="rotate(-30 29 26)" />
    <polygon points="27,32 29,34 26,35" fill="currentColor" opacity=".25" transform="rotate(-30 29 26)" />
  </g>
);

const PmIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* clipboard */}
    <rect x="27" y="22" width="9" height="12" rx="1" fill="currentColor" opacity=".25" />
    <rect x="29" y="20.5" width="5" height="2.5" rx="1" fill="currentColor" opacity=".35" />
    <line x1="29" y1="26" x2="34" y2="26" stroke="currentColor" strokeWidth=".6" opacity=".4" />
    <line x1="29" y1="28.5" x2="34" y2="28.5" stroke="currentColor" strokeWidth=".6" opacity=".4" />
    <line x1="29" y1="31" x2="33" y2="31" stroke="currentColor" strokeWidth=".6" opacity=".4" />
  </g>
);

const AnalystIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* bar chart */}
    <rect x="27" y="30" width="2.5" height="6" rx=".3" fill="currentColor" opacity=".3" />
    <rect x="30.5" y="27" width="2.5" height="9" rx=".3" fill="currentColor" opacity=".35" />
    <rect x="34" y="32" width="2.5" height="4" rx=".3" fill="currentColor" opacity=".25" />
  </g>
);

const MarketerIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* trending arrow up */}
    <polyline points="10,36 16,30 22,33 30,24" fill="none" stroke="currentColor" strokeWidth="1.5" opacity=".3" strokeLinecap="round" />
    <polygon points="28,22 32,24 30,27" fill="currentColor" opacity=".3" />
  </g>
);

const WriterIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* pen nib */}
    <path d="M30 20 L34 28 L30 30 L26 28 Z" fill="currentColor" opacity=".25" />
    <line x1="30" y1="20" x2="30" y2="16" stroke="currentColor" strokeWidth="1" opacity=".3" />
  </g>
);

const HrIcon = (c = "#fff") => (
  <g>
    <Head cy={12} r={5.5} fill={c} />
    <path d="M10 36 C10 28 15 24 20 24 C25 24 30 28 30 36" fill={c} />
    {/* second person behind */}
    <circle cx="30" cy="14" r="4.5" fill={c} opacity=".5" />
    <path d="M22 38 C22 32 25 28 30 28 C35 28 38 32 38 38" fill={c} opacity=".5" />
  </g>
);

const LegalIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* scales of justice */}
    <line x1="30" y1="20" x2="30" y2="32" stroke="currentColor" strokeWidth="1" opacity=".3" />
    <line x1="25" y1="24" x2="35" y2="24" stroke="currentColor" strokeWidth="1" opacity=".3" />
    <path d="M24 24 L22.5 28 L27.5 28 Z" fill="currentColor" opacity=".25" />
    <path d="M36 24 L34.5 28 L38 28 Z" fill="currentColor" opacity=".25" />
  </g>
);

const SupportIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* headset */}
    <path d="M12 15 Q12 8 20 8 Q28 8 28 15" fill="none" stroke="currentColor" strokeWidth="1.5" opacity=".3" />
    <rect x="10" y="14" width="3" height="5" rx="1" fill="currentColor" opacity=".3" />
    <rect x="27" y="14" width="3" height="5" rx="1" fill="currentColor" opacity=".3" />
    <path d="M11 19 Q11 22 14 22" fill="none" stroke="currentColor" strokeWidth="1" opacity=".25" />
  </g>
);

const ResearcherIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* magnifying glass */}
    <circle cx="31" cy="27" r="4" fill="none" stroke="currentColor" strokeWidth="1.2" opacity=".3" />
    <line x1="34" y1="30" x2="37" y2="33" stroke="currentColor" strokeWidth="1.5" opacity=".3" strokeLinecap="round" />
  </g>
);

const MediaIcon = (c = "#fff") => (
  <g>
    <Head fill={c} />
    <Shoulders fill={c} />
    {/* phone */}
    <rect x="28" y="22" width="6" height="10" rx="1.2" fill="currentColor" opacity=".3" />
    <circle cx="31" cy="30" r=".6" fill="currentColor" opacity=".5" />
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
              width: size,
              height: size,
              borderRadius: radius,
              objectFit: "cover",
              display: "block",
            }}
          />
        ) : (
          <svg
            viewBox="0 0 40 40"
            width={size}
            height={size}
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
