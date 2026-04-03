import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../utils/chatTypes";
import { SVG_PATHS } from "../utils/chatHelpers";

export function RenderIcon({ icon, size = 14 }: { icon: string; size?: number }) {
  if (icon.startsWith("svg:")) {
    const d = SVG_PATHS[icon.slice(4)];
    if (!d) return <span>{icon}</span>;
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        <path d={d} />
      </svg>
    );
  }
  return <>{icon}</>;
}

const FADE_DELAY_MS = 30_000;

export function SubAgentCards({ tasks }: { tasks: SubAgentTask[] }) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [fadedIds, setFadedIds] = useState<Set<string>>(new Set());
  const fadedIdsRef = useRef(fadedIds);
  fadedIdsRef.current = fadedIds;
  const fadeTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const PAGE_SIZE = 4;

  useEffect(() => {
    const timers = fadeTimersRef.current;
    for (const task of tasks) {
      const isDone = task.status === "completed" || task.status === "error" || task.status === "cancelled" || task.status === "timeout";
      if (isDone && !fadedIdsRef.current.has(task.agent_id) && !timers.has(task.agent_id)) {
        timers.set(task.agent_id, setTimeout(() => {
          setFadedIds((prev) => new Set(prev).add(task.agent_id));
          timers.delete(task.agent_id);
        }, FADE_DELAY_MS));
      } else if (!isDone && timers.has(task.agent_id)) {
        clearTimeout(timers.get(task.agent_id));
        timers.delete(task.agent_id);
        setFadedIds((prev) => {
          const next = new Set(prev);
          next.delete(task.agent_id);
          return next;
        });
      }
    }
  }, [tasks]);

  useEffect(() => {
    return () => {
      for (const timer of fadeTimersRef.current.values()) clearTimeout(timer);
    };
  }, []);

  const visibleTasks = tasks.filter((t) => !fadedIds.has(t.agent_id));
  const totalPages = Math.max(1, Math.ceil(visibleTasks.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const visible = visibleTasks.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  const toggleExpand = useCallback((id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  const restoreFaded = useCallback(() => {
    setFadedIds(new Set());
  }, []);

  const statusLabel = (s: string) => {
    switch (s) {
      case "starting": return t("chat.subAgentStarting", "启动中");
      case "running": return t("chat.subAgentRunning", "执行中");
      case "completed": return t("chat.subAgentDone", "已完成");
      case "error": return t("chat.subAgentError", "出错");
      case "timeout": return t("chat.subAgentTimeout", "超时");
      case "cancelled": return t("chat.subAgentCancelled", "已取消");
      default: return s;
    }
  };

  const statusClass = (s: string) => {
    switch (s) {
      case "starting":
      case "running": return "sacBadgeRunning";
      case "completed": return "sacBadgeDone";
      case "error": return "sacBadgeError";
      case "timeout": return "sacBadgeTimeout";
      default: return "";
    }
  };

  const formatElapsed = (s: number) => {
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}m${sec > 0 ? sec + "s" : ""}`;
  };

  const formatTokens = (n: number | undefined) => {
    if (n == null) return null;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  };

  return (
    <div className="sacContainer">
      <div className="sacHeader">
        <span className="sacTitle">{t("chat.subAgentPanel", "子 Agent 进度")}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {fadedIds.size > 0 && (
            <button className="sacPageBtn" onClick={restoreFaded} title={t("chat.showCompleted", "显示已完成")}>
              +{fadedIds.size}
            </button>
          )}
          {totalPages > 1 && (
            <div className="sacPager">
              <button className="sacPageBtn" disabled={safePage <= 0} onClick={() => setPage(p => p - 1)}>‹</button>
              <span className="sacPageInfo">{safePage + 1}/{totalPages}</span>
              <button className="sacPageBtn" disabled={safePage >= totalPages - 1} onClick={() => setPage(p => p + 1)}>›</button>
            </div>
          )}
        </div>
      </div>
      <div className="sacGrid" ref={scrollRef}>
        {visible.map((task) => {
          const isExpanded = expandedId === task.agent_id;
          return (
            <div
              key={task.agent_id}
              className={`sacCard ${task.status === "running" || task.status === "starting" ? "sacCardActive" : ""}`}
              onClick={() => toggleExpand(task.agent_id)}
              style={{ cursor: "pointer" }}
            >
              <div className="sacCardTop">
                <span className="sacIcon"><RenderIcon icon={task.icon} size={16} /></span>
                <span className="sacName">{task.name}</span>
                <span className={`sacBadge ${statusClass(task.status)}`}>
                  {(task.status === "running" || task.status === "starting") && <span className="sacPulse" />}
                  {statusLabel(task.status)}
                </span>
              </div>
              <div className="sacCardMeta">
                <span>{t("chat.subAgentIter", "迭代")} {task.iteration}</span>
                <span className="sacDot">·</span>
                <span>{formatElapsed(task.elapsed_s)}</span>
                <span className="sacDot">·</span>
                <span>{t("chat.subAgentTools", "工具")} ×{task.tools_total}</span>
                {formatTokens(task.tokens_used) && (
                  <>
                    <span className="sacDot">·</span>
                    <span>{formatTokens(task.tokens_used)} tokens</span>
                  </>
                )}
                {task.queue_count != null && task.queue_count > 0 && (
                  <>
                    <span className="sacDot">·</span>
                    <span>{t("chat.subAgentQueue", "排队")} {task.queue_count}</span>
                  </>
                )}
              </div>
              {task.current_tool_summary && (
                <div className="sacToolSummary">
                  {task.current_tool_summary}
                </div>
              )}
              <div className="sacToolList">
                {(task.tools_executed ?? []).length === 0 && (
                  <div className="sacToolItem sacToolWaiting">…</div>
                )}
                {(isExpanded ? (task.tools_executed ?? []) : (task.tools_executed ?? []).slice(-3)).map((tool, idx) => {
                  const tools = task.tools_executed ?? [];
                  const actualIdx = isExpanded || tools.length <= 3 ? idx : tools.length - 3 + idx;
                  const isCurrent = actualIdx === tools.length - 1 && (task.status === "running" || task.status === "starting");
                  return (
                    <div key={`${tool}-${actualIdx}`} className={`sacToolItem ${isCurrent ? "sacToolCurrent" : ""}`}>
                      <span className="sacToolArrow">{isCurrent ? "▸" : "▹"}</span>
                      <span className="sacToolName">{tool}</span>
                      {isCurrent && <span className="sacToolBlink" />}
                    </div>
                  );
                })}
                {!isExpanded && (task.tools_executed ?? []).length > 3 && (
                  <div className="sacToolItem" style={{ opacity: 0.5, fontSize: 11 }}>
                    ... {(task.tools_executed ?? []).length - 3} {t("chat.more", "更多")}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
