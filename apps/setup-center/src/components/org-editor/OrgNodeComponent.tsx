import { useState, useRef } from "react";
import { createPortal } from "react-dom";
import { Handle, Position, type NodeTypes } from "@xyflow/react";
import { IconAlertCircle, IconSnowflake } from "../../icons";
import { OrgAvatar } from "../OrgAvatars";
import { STATUS_COLORS, STATUS_LABELS, getDeptColor, fmtTime, fmtShortDate } from "./helpers";
import type { OrgNodeData } from "./types";

export function OrgNodeComponent({ data, selected }: { data: OrgNodeData; selected: boolean }) {
  const [hovered, setHovered] = useState(false);
  const nodeRef = useRef<HTMLDivElement>(null);
  const deptColor = getDeptColor(data.department);
  const statusColor = STATUS_COLORS[data.status] || "var(--muted)";
  const isFrozen = data.status === "frozen";
  const isBusy = data.status === "busy";
  const isError = data.status === "error";
  const isWaiting = data.status === "waiting";
  const isClone = data.is_clone;
  const isEphemeral = data.ephemeral;

  const rt = (data as any)._runtime;
  const idleSecs = rt?.idle_seconds;
  const pendingMsgs = rt?.pending_messages;
  const isAnomaly = rt?.anomaly;

  return (
    <div
      ref={nodeRef}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: "var(--card-bg, #fff)",
        border: `2px solid ${selected ? "var(--primary)" : isAnomaly ? "#f59e0b" : isError ? "var(--danger)" : isBusy ? statusColor : "var(--line)"}`,
        borderRadius: "var(--radius)",
        padding: 0,
        minWidth: 180,
        maxWidth: 220,
        boxShadow: selected
          ? "0 0 0 2px var(--primary)"
          : isAnomaly
          ? "0 0 12px rgba(245,158,11,0.35)"
          : isBusy
          ? `0 0 16px ${statusColor}50`
          : isError
          ? `0 0 12px var(--danger, #ef4444)30`
          : "0 1px 4px rgba(0,0,0,0.08)",
        opacity: isFrozen ? 0.5 : 1,
        filter: isFrozen ? "grayscale(0.6)" : "none",
        transition: "all 0.3s ease",
        animation: isBusy
          ? "orgNodePulse 2s ease-in-out infinite"
          : isError
          ? "orgNodeError 1s ease-in-out infinite"
          : isWaiting
          ? "orgNodeWait 3s ease-in-out infinite"
          : "none",
        position: "relative",
        zIndex: hovered ? 10000 : "auto",
      }}
    >
      <Handle type="target" position={Position.Top} className="org-handle" />

      <div style={{
        height: 4,
        borderRadius: "var(--radius) var(--radius) 0 0",
        background: isBusy
          ? `linear-gradient(90deg, ${deptColor}, ${statusColor}, ${deptColor})`
          : isAnomaly
          ? "linear-gradient(90deg, #f59e0b, #fbbf24, #f59e0b)"
          : deptColor,
        backgroundSize: isBusy || isAnomaly ? "200% 100%" : undefined,
        animation: isBusy ? "orgStripFlow 2s linear infinite" : isAnomaly ? "orgStripFlow 3s linear infinite" : undefined,
      }} />

      <div style={{ padding: "8px 10px", display: "flex", gap: 8, alignItems: "flex-start" }}>
        <OrgAvatar
          avatarId={data.avatar}
          size={30}
          statusColor={statusColor}
          statusGlow={isBusy}
          style={isBusy ? { border: `2px solid ${statusColor}` } : isError ? { border: "2px solid var(--danger)" } : undefined}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
            <span style={{
              fontSize: 13, fontWeight: 600,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
            }}>
              {data.role_title}
            </span>
            {(isClone || isEphemeral) && (
              <span style={{
                fontSize: 9, padding: "0 4px", borderRadius: 3,
                background: isEphemeral ? "#fef3c7" : "#e0f2fe",
                color: isEphemeral ? "#b45309" : "#0369a1",
                fontWeight: 500,
              }}>
                {isEphemeral ? "临时" : "副本"}
              </span>
            )}
          </div>

          {data.role_goal && (
            <div style={{
              fontSize: 10, color: "var(--muted)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              marginBottom: 4, maxWidth: 180,
            }}>
              {data.role_goal}
            </div>
          )}

          <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
            {data.department && (
              <span style={{
                fontSize: 10, padding: "1px 6px", borderRadius: 4,
                background: `${deptColor}15`, color: deptColor, fontWeight: 500,
              }}>
                {data.department}
              </span>
            )}
            {data.status !== "idle" && (
              <span style={{
                fontSize: 10, padding: "1px 6px", borderRadius: 4,
                background: `${statusColor}15`, color: statusColor, fontWeight: 500,
              }}>
                {STATUS_LABELS[data.status] || data.status}
              </span>
            )}
            {pendingMsgs > 0 && (
              <span style={{
                fontSize: 9, padding: "1px 5px", borderRadius: 10,
                background: "#fef2f2", color: "#dc2626", fontWeight: 600,
              }}>
                {pendingMsgs}
              </span>
            )}
            {idleSecs != null && idleSecs > 60 && data.status === "idle" && (
              <span style={{
                fontSize: 9, padding: "1px 5px", borderRadius: 3,
                background: "#f3f4f6", color: "#9ca3af",
              }}>
                {idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h` : `${Math.floor(idleSecs / 60)}m`}
              </span>
            )}
          </div>

          {isBusy && data.current_task && (
            <div style={{
              fontSize: 9, color: statusColor, marginTop: 3,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              maxWidth: 180, fontStyle: "italic", opacity: 0.85,
            }}>
              {data.current_task.slice(0, 40)}{data.current_task.length > 40 ? "..." : ""}
            </div>
          )}

          {isAnomaly && (
            <div style={{ fontSize: 9, color: "#f59e0b", marginTop: 3, display: "flex", alignItems: "center", gap: 3 }}>
              <IconAlertCircle size={10} color="#f59e0b" />
              <span>{typeof isAnomaly === "string" ? isAnomaly : "需要关注"}</span>
            </div>
          )}

          {isFrozen && (
            <div style={{ fontSize: 10, color: "#93c5fd", marginTop: 4, display: "flex", alignItems: "center", gap: 3 }}>
              <IconSnowflake size={11} color="#93c5fd" />
              <span>{data.frozen_reason || "已冻结"}</span>
            </div>
          )}
        </div>
      </div>

      {hovered && rt && nodeRef.current && createPortal(
        (() => {
          const rect = nodeRef.current!.getBoundingClientRect();
          const pp = rt.plan_progress as { completed?: number; total?: number } | undefined;
          const ds = rt.delegated_summary as { in_progress?: number; completed?: number; total?: number } | undefined;
          const extTools = (rt.external_tools as string[] | undefined) || [];
          const runningSince = rt.running_since as string | number | undefined;
          const recentTs = rt.recent_activity_ts as string | number | undefined;
          const watchdog = rt.last_watchdog_action as string | undefined;
          const Sep = () => <div style={{ height: 1, background: "var(--line)", margin: "6px 0" }} />;
          return (
            <div style={{
              position: "fixed", left: rect.right + 8, top: rect.top, zIndex: 99999,
              background: "var(--card-bg, #fff)", border: "1px solid var(--line)",
              borderRadius: 6, padding: "10px 12px", minWidth: 240,
              pointerEvents: "none",
              boxShadow: "0 4px 12px rgba(0,0,0,0.15)", fontSize: 10,
            }}>
              <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 11 }}>{data.role_title}</div>
              <div style={{ color: "#6b7280", lineHeight: 1.6 }}>
                <div>部门: {data.department || "—"} · 层级 L{data.level ?? "?"}</div>
                <div>状态: <span style={{ color: statusColor, fontWeight: 500 }}>{STATUS_LABELS[data.status] || data.status}</span></div>
                {idleSecs != null && <div>空闲: {idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h${Math.floor((idleSecs % 3600) / 60)}m` : idleSecs >= 60 ? `${Math.floor(idleSecs / 60)}m` : `${idleSecs}s`}</div>}
                {pendingMsgs != null && pendingMsgs > 0 && <div>待处理: {pendingMsgs} 条消息</div>}
                <Sep />
                {pp && pp.total != null && pp.total > 0 && (
                  <div>
                    计划进度: {pp.completed ?? 0}/{pp.total}
                    <div style={{ marginTop: 2, height: 4, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
                      <div style={{ height: "100%", width: `${Math.min(100, ((pp.completed ?? 0) / pp.total) * 100)}%`, background: "var(--primary)", borderRadius: 2 }} />
                    </div>
                  </div>
                )}
                {ds && (ds.total ?? 0) > 0 && (
                  <div>委派: 进行中 {ds.in_progress ?? 0} · 已完成 {ds.completed ?? 0} / {ds.total}</div>
                )}
                <Sep />
                {runningSince != null && (
                  <div>运行中: {typeof runningSince === "number" ? fmtTime(runningSince) : fmtShortDate(runningSince)}</div>
                )}
                {extTools.length > 0 && <div>外部工具: {extTools.slice(0, 3).join(", ")}{extTools.length > 3 ? ` +${extTools.length - 3}` : ""}</div>}
                {recentTs != null && <div>最近活动: {fmtShortDate(recentTs)}</div>}
                {watchdog && <div>看门狗: {watchdog}</div>}
                <Sep />
                {data.current_task && <div style={{ marginTop: 2, color: "#b45309" }}>任务: {data.current_task.slice(0, 50)}</div>}
                {isAnomaly && <div style={{ marginTop: 2, color: "#f59e0b", fontWeight: 500 }}>{typeof isAnomaly === "string" ? isAnomaly : "异常"}</div>}
              </div>
            </div>
          );
        })(),
        document.body,
      )}

      <Handle type="source" position={Position.Bottom} className="org-handle" />
    </div>
  );
}

export const nodeTypes: NodeTypes = {
  orgNode: OrgNodeComponent as any,
};
