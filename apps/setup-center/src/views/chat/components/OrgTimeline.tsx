import { memo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { OrgTimelineEntry } from "../../../types";

/**
 * Live timeline for organization-command progress (org-mode only).
 *
 * Rendered above the assistant's final answer so users can:
 *   - 实时看到组织里的哪个节点在干活 / 进展到哪一步；
 *   - 命令完成后把整个过程折叠起来，不污染最终回答；
 *   - 不再用 blockquote 把进度文本塞进 `msg.content` 里。
 *
 * 仅当 ``entries`` 非空时渲染。
 */
function statusDot(status: OrgTimelineEntry["status"]): string {
  if (status === "started") return "●";
  if (status === "done") return "✓";
  return "▸";
}

function statusColor(status: OrgTimelineEntry["status"]): string {
  if (status === "done") return "var(--success, #10b981)";
  if (status === "started") return "var(--brand, #6366f1)";
  return "var(--text-secondary, #94a3b8)";
}

export const OrgTimelineCard = memo(function OrgTimelineCard({
  entries,
  streaming,
}: {
  entries: OrgTimelineEntry[];
  streaming?: boolean;
}) {
  const { t } = useTranslation();
  const done = entries.some((e) => e.status === "done");
  // Streaming → 默认展开；完成后默认折叠，避免长进度淹没回答。
  const [collapsed, setCollapsed] = useState<boolean>(done && !streaming);

  if (!entries || entries.length === 0) return null;

  const progressCount = entries.filter((e) => e.status === "progress").length;
  const headerLabel = done
    ? t("chat.orgTimelineDone", "组织协同已完成 · {{n}} 步", { n: progressCount })
    : t("chat.orgTimelineRunning", "组织协同中 · 已收到 {{n}} 条进度", { n: progressCount });

  return (
    <div
      style={{
        marginBottom: 8,
        border: "1px solid var(--line)",
        borderRadius: 8,
        background: "var(--panel, rgba(99,102,241,0.04))",
        overflow: "hidden",
        fontSize: 12,
      }}
    >
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          background: "transparent",
          border: "none",
          color: "var(--text)",
          cursor: "pointer",
          fontWeight: 600,
          fontSize: 12,
          textAlign: "left",
        }}
      >
        <span style={{ color: done ? "var(--success, #10b981)" : "var(--brand, #6366f1)" }}>
          {done ? "✓" : "◐"}
        </span>
        <span style={{ flex: 1 }}>{headerLabel}</span>
        <span style={{ opacity: 0.5, fontSize: 11 }}>{collapsed ? "▾" : "▴"}</span>
      </button>
      {!collapsed && (
        <ol
          style={{
            margin: 0,
            padding: "0 12px 10px 12px",
            listStyle: "none",
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          {entries.map((entry, idx) => (
            <li
              key={`${entry.timestamp}-${idx}`}
              style={{
                display: "flex",
                gap: 8,
                lineHeight: 1.55,
                opacity: entry.status === "done" ? 0.85 : 1,
              }}
            >
              <span
                style={{
                  color: statusColor(entry.status),
                  fontFamily: "var(--font-mono, monospace)",
                  flex: "0 0 14px",
                  textAlign: "center",
                }}
              >
                {statusDot(entry.status)}
              </span>
              <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {entry.category && (
                  <span style={{ opacity: 0.55, marginRight: 6 }}>[{entry.category}]</span>
                )}
                {entry.summary || (entry.status === "started" ? t("chat.orgTimelineStarted", "命令已下发") : "")}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
});
