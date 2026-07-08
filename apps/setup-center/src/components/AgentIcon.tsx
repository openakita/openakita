import React from "react";

export const AGENT_SVG_ICONS: Record<string, { path: string; label: string }> = {
  terminal: { label: "终端", path: "M4 17l6-5-6-5M12 19h8" },
  code: { label: "代码", path: "M16 18l6-6-6-6M8 6l-6 6 6 6" },
  globe: { label: "全球", path: "M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10A15.3 15.3 0 0112 2z" },
  shield: { label: "安全", path: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" },
  database: { label: "数据库", path: "M12 2C6.48 2 2 3.79 2 6v12c0 2.21 4.48 4 10 4s10-1.79 10-4V6c0-2.21-4.48-4-10-4zM2 12c0 2.21 4.48 4 10 4s10-1.79 10-4M2 6c0 2.21 4.48 4 10 4s10-1.79 10-4" },
  cpu: { label: "芯片", path: "M6 6h12v12H6zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" },
  cloud: { label: "云", path: "M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z" },
  lock: { label: "锁", path: "M19 11H5a2 2 0 00-2 2v7a2 2 0 002 2h14a2 2 0 002-2v-7a2 2 0 00-2-2zM7 11V7a5 5 0 0110 0v4" },
  zap: { label: "闪电", path: "M13 2L3 14h9l-1 8 10-12h-9l1-8z" },
  eye: { label: "监控", path: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 100 6 3 3 0 000-6z" },
  message: { label: "对话", path: "M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" },
  mail: { label: "邮件", path: "M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6" },
  chart: { label: "图表", path: "M18 20V10M12 20V4M6 20v-6" },
  network: { label: "网络", path: "M5.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM18.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM12 24a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM5.5 5.5L12 19M18.5 5.5L12 19" },
  target: { label: "靶心", path: "M12 2a10 10 0 100 20 10 10 0 000-20zM12 6a6 6 0 100 12 6 6 0 000-12zM12 10a2 2 0 100 4 2 2 0 000-4z" },
  compass: { label: "指南", path: "M12 2a10 10 0 100 20 10 10 0 000-20zM16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36z" },
  layers: { label: "层级", path: "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" },
  workflow: { label: "流程", path: "M6 3a3 3 0 100 6 3 3 0 000-6zM18 15a3 3 0 100 6 3 3 0 000-6zM8.59 13.51l6.83 3.98M6 9v4M18 9v6" },
  flask: { label: "实验", path: "M9 3h6M10 3v6.5l-5 8.5h14l-5-8.5V3" },
  pen: { label: "创作", path: "M12 20h9M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4L16.5 3.5z" },
  mic: { label: "语音", path: "M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8" },
  bot: { label: "机器人", path: "M12 2a2 2 0 012 2v1h3a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2h3V4a2 2 0 012-2zM9 13h0M15 13h0M9 17h6" },
  puzzle: { label: "拼图", path: "M19.439 12.956l-1.5 0a2 2 0 010-4l1.5 0a.5.5 0 00.5-.5l0-2.5a2 2 0 00-2-2l-2.5 0a.5.5 0 01-.5-.5l0-1.5a2 2 0 00-4 0l0 1.5a.5.5 0 01-.5.5L7.939 3.956a2 2 0 00-2 2l0 2.5a.5.5 0 00.5.5l1.5 0a2 2 0 010 4l-1.5 0a.5.5 0 00-.5.5l0 2.5a2 2 0 002 2l2.5 0a.5.5 0 01.5.5l0 1.5a2 2 0 004 0l0-1.5a.5.5 0 01.5-.5l2.5 0a2 2 0 002-2l0-2.5a.5.5 0 00-.5-.5z" },
  heart: { label: "爱心", path: "M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 000-7.78z" },
};

export function isCustomAgentIcon(icon: string | null | undefined): boolean {
  if (!icon) return false;
  return icon.startsWith("/") || icon.startsWith("http://") || icon.startsWith("https://");
}

export function resolveAgentIconUrl(icon: string, apiBaseUrl = ""): string {
  if (icon.startsWith("http://") || icon.startsWith("https://")) return icon;
  if (icon.startsWith("/") && apiBaseUrl) return `${apiBaseUrl.replace(/\/+$/, "")}${icon}`;
  return icon;
}

export function agentIconText(icon: string | null | undefined): string {
  if (!icon) return "🤖";
  return isCustomAgentIcon(icon) ? "🖼️" : icon;
}

export function AgentIcon({
  icon,
  color = "currentColor",
  size = 16,
  apiBaseUrl = "",
  className,
  style,
  fallback,
}: {
  icon?: string | null;
  color?: string;
  size?: number;
  apiBaseUrl?: string;
  className?: string;
  style?: React.CSSProperties;
  fallback?: React.ReactNode;
}) {
  const value = icon || "";
  if (isCustomAgentIcon(value)) {
    const radius = Math.max(Math.round(size * 0.2), 4);
    return (
      <img
        src={resolveAgentIconUrl(value, apiBaseUrl)}
        alt=""
        className={className}
        style={{
          width: size,
          height: size,
          borderRadius: radius,
          display: "block",
          objectFit: "cover",
          flexShrink: 0,
          ...style,
        }}
      />
    );
  }
  if (value.startsWith("svg:")) {
    const meta = AGENT_SVG_ICONS[value.slice(4)];
    if (!meta) return fallback ?? <span className={className} style={style}>?</span>;
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        style={{ flexShrink: 0, ...style }}
      >
        <path d={meta.path} />
      </svg>
    );
  }
  if (!value) return fallback ?? null;
  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        fontSize: Math.round(size * 0.9),
        lineHeight: 1,
        flexShrink: 0,
        ...style,
      }}
    >
      {value}
    </span>
  );
}
