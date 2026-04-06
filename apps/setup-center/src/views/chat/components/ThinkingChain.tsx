import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import type { ChainGroup, ChainEntry, ChatToolCall } from "../utils/chatTypes";
import {
  IconChevronRight, IconCheck, IconX, IconLoader, IconCircle,
} from "../../../icons";

// ── ThinkingBlock: legacy bubble mode ──

export function ThinkingBlock({ content, defaultOpen }: { content: string; defaultOpen?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="thinkingBlock">
      <div
        className="thinkingHeader"
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 0", userSelect: "none" }}
      >
        <span style={{ fontSize: 12, opacity: 0.5, transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s", display: "inline-flex", alignItems: "center" }}><IconChevronRight size={12} /></span>
        <span style={{ fontWeight: 700, fontSize: 13, opacity: 0.6 }}>{t("chat.thinkingBlock")}</span>
      </div>
      {open && (
        <div style={{ padding: "8px 12px", background: "rgba(124,58,237,0.04)", borderRadius: 10, fontSize: 13, lineHeight: 1.6, opacity: 0.75, whiteSpace: "pre-wrap" }}>
          {content}
        </div>
      )}
    </div>
  );
}

// ── ToolCallDetail ──

export function ToolCallDetail({ tc }: { tc: ChatToolCall }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const statusIcon =
    tc.status === "done" ? <IconCheck size={14} /> :
    tc.status === "error" ? <IconX size={14} /> :
    tc.status === "running" ? <IconLoader size={14} /> :
    <IconCircle size={10} />;
  const statusColor = tc.status === "done" ? "var(--ok)" : tc.status === "error" ? "var(--danger)" : "var(--brand)";
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", background: "rgba(37,99,235,0.03)", userSelect: "none" }}
      >
        <span style={{ color: statusColor, fontWeight: 800, display: "inline-flex", alignItems: "center" }}>{statusIcon}</span>
        <span style={{ fontWeight: 600, fontSize: 12 }}>{tc.tool}</span>
        <span style={{ fontSize: 10, opacity: 0.4, marginLeft: "auto" }}>{open ? t("chat.collapse") : t("chat.expand")}</span>
      </div>
      {open && (
        <div style={{ padding: "6px 10px", fontSize: 12, background: "var(--panel)" }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{t("chat.args")}</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>
            {JSON.stringify(tc.args, null, 2)}
          </pre>
          {tc.result != null && (
            <>
              <div style={{ fontWeight: 700, marginTop: 8, marginBottom: 4 }}>{t("chat.result")}</div>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11, maxHeight: 200, overflow: "auto" }}>
                {typeof tc.result === "string" ? tc.result : JSON.stringify(tc.result, null, 2)}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── ToolCallsGroup ──

export function ToolCallsGroup({ toolCalls }: { toolCalls: ChatToolCall[] }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  const doneCount = toolCalls.filter((tc) => tc.status === "done").length;
  const errorCount = toolCalls.filter((tc) => tc.status === "error").length;
  const runningCount = toolCalls.filter((tc) => tc.status === "running").length;
  const hasError = errorCount > 0;
  const summaryColor = hasError ? "var(--danger)" : runningCount > 0 ? "var(--brand)" : "var(--ok)";
  const summaryIcon = hasError ? <IconX size={14} /> : runningCount > 0 ? <IconLoader size={14} /> : <IconCheck size={14} />;
  const toolNames = toolCalls.map((tc) => tc.tool);
  const nameCounts: Record<string, number> = {};
  for (const n of toolNames) nameCounts[n] = (nameCounts[n] || 0) + 1;
  const nameLabels = Object.entries(nameCounts).map(([n, c]) => c > 1 ? `${n} ×${c}` : n);
  const summaryText = nameLabels.join(", ");

  return (
    <div style={{ margin: "6px 0", border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", background: "rgba(37,99,235,0.04)", userSelect: "none" }}
      >
        <span style={{ color: summaryColor, fontWeight: 800, display: "inline-flex", alignItems: "center" }}>{summaryIcon}</span>
        <span style={{ fontWeight: 700, fontSize: 13 }}>
          {t("chat.toolCallLabel")}{toolCalls.length > 1 ? `${toolCalls.length} ` : ""}{toolCalls.length === 1 ? toolCalls[0].tool : ""}
        </span>
        {toolCalls.length > 1 && (
          <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
            {summaryText}
          </span>
        )}
        <span style={{ fontSize: 11, opacity: 0.5, marginLeft: "auto", flexShrink: 0 }}>{expanded ? t("chat.collapse") : t("chat.expand")}</span>
      </div>
      {expanded && (
        <div style={{ padding: "6px 8px", display: "flex", flexDirection: "column", gap: 4, background: "var(--panel)" }}>
          {toolCalls.map((tc, i) => (
            <ToolCallDetail key={i} tc={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── ToolResultBlock ──

function ToolResultBlock({ result }: { result: string }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  if (!result) return null;
  const safeResult = typeof result === "string" ? result : JSON.stringify(result, null, 2);
  const isShort = safeResult.length < 120;
  if (isShort) return <span className="chainToolResultInline">{safeResult}</span>;
  return (
    <span className="chainToolResultCollapsible">
      <span className="chainToolResultToggle" onClick={() => setExpanded(v => !v)}>
        {expanded ? t("common.collapse", "收起") : t("common.viewDetails", "查看详情")} <IconChevronRight size={9} />
      </span>
      {expanded && <pre className="chainToolResult">{safeResult}</pre>}
    </span>
  );
}

// ── ChainEntryLine ──

function ChainEntryLine({ entry, onSkipStep }: { entry: ChainEntry; onSkipStep?: () => void }) {
  const { t } = useTranslation();
  switch (entry.kind) {
    case "thinking":
      return (
        <div className="chainNarrThinking">
          <span className="chainNarrThinkingLabel">thinking</span>
          <span className="chainNarrThinkingText">{entry.content}</span>
        </div>
      );
    case "text":
      return <div className="chainNarrText">{entry.content}</div>;
    case "tool_start": {
      const isRunning = entry.status === "running";
      const tsIcon = entry.status === "error"
        ? <IconX size={11} />
        : entry.status === "done"
          ? <IconCheck size={11} />
          : <IconLoader size={11} className="chainSpinner" />;
      return (
        <div className={`chainNarrToolStart ${isRunning ? "chainNarrToolRunning" : ""}`} data-tool-id={entry.toolId}>
          {tsIcon}
          <span className="chainNarrToolName">{entry.description || entry.tool}</span>
          {isRunning && (
            <span className="chainToolElapsed" />
          )}
          {isRunning && onSkipStep && (
            <button
              data-slot="skip"
              className="chainToolSkipBtn"
              onClick={(e) => { e.stopPropagation(); onSkipStep(); }}
              title="Skip this step"
            >
              <IconX size={10} />
            </button>
          )}
        </div>
      );
    }
    case "tool_end": {
      const isError = entry.status === "error";
      const icon = isError ? <IconX size={11} /> : <IconCheck size={11} />;
      const cls = isError ? "chainNarrToolEnd chainNarrToolError" : "chainNarrToolEnd";
      return (
        <div className={cls}>
          {icon}
          <ToolResultBlock result={entry.result} />
        </div>
      );
    }
    case "compressed":
      return (
        <div className="chainNarrCompressed">
          {t("chat.contextCompressed", "上下文压缩: {{before}}k → {{after}}k tokens", { before: Math.round(entry.beforeTokens / 1000), after: Math.round(entry.afterTokens / 1000) })}
        </div>
      );
    default:
      return null;
  }
}

// ── ChainGroupItem ──

function ChainGroupItem({ group, onToggle, isLast, streaming, onSkipStep }: {
  group: ChainGroup;
  onToggle: () => void;
  isLast: boolean;
  streaming: boolean;
  onSkipStep?: () => void;
}) {
  const { t } = useTranslation();
  const isActive = isLast && streaming;
  const durMs = group.durationMs;
  const durationSec = durMs ? (durMs / 1000).toFixed(1) : null;
  const hasContent = group.entries.length > 0;

  if (!hasContent && !isActive) {
    return (
      <div className="chainGroup chainGroupCompact">
        <div className="chainProcessedLine">
          <IconCheck size={11} />
          <span>{t("chat.processed", { seconds: durationSec || "0" })}</span>
        </div>
      </div>
    );
  }

  const showContent = !group.collapsed || isActive;
  const headerLabel = isActive
    ? t("chat.processing")
    : group.hasThinking
      ? t("chat.thoughtFor", { seconds: durationSec || "0" })
      : t("chat.processed", { seconds: durationSec || "0" });

  return (
    <div className={`chainGroup ${group.collapsed && !isActive ? "chainGroupCollapsed" : ""}`}>
      <div className="chainThinkingHeader" onClick={onToggle}>
        <span className="chainChevron" style={{ transform: showContent ? "rotate(90deg)" : "rotate(0deg)" }}>
          <IconChevronRight size={11} />
        </span>
        <span className={`chainThinkingLabel ${isActive ? "chainThinkingLabelActive" : ""}`}>{headerLabel}</span>
        {isActive && <IconLoader size={16} className="chainSpinner chainSpinnerActive" />}
      </div>
      {showContent && (
        <div className="chainNarrFlow">
          {group.entries.map((entry: ChainEntry, i: number) => (
            <ChainEntryLine key={i} entry={entry} onSkipStep={onSkipStep} />
          ))}
          {isActive && group.entries.length > 0 && (
            <div className="chainNarrCursor" />
          )}
        </div>
      )}
    </div>
  );
}

// ── ThinkingChain (main export) ──

export function ThinkingChain({ chain, streaming, showChain, onSkipStep }: {
  chain: ChainGroup[];
  streaming: boolean;
  showChain: boolean;
  onSkipStep?: () => void;
}) {
  const { t } = useTranslation();
  const [localChain, setLocalChain] = useState(chain);

  useEffect(() => {
    setLocalChain(prev => {
      const prevMap = new Map(prev.map(g => [g.iteration, g.collapsed]));
      return chain.map(g => ({
        ...g,
        collapsed: prevMap.has(g.iteration) ? prevMap.get(g.iteration)! : g.collapsed,
      }));
    });
  }, [chain]);

  if (!showChain || !localChain || localChain.length === 0) return null;

  const allCollapsed = localChain.every(g => g.collapsed) && !streaming;
  if (allCollapsed) {
    const totalSteps = localChain.reduce((n, g) => n + g.entries.length, 0);
    return (
      <div
        className="chainCollapsedSummary"
        onClick={() => setLocalChain(prev => prev.map(g => ({ ...g, collapsed: false })))}
      >
        <IconChevronRight size={11} />
        <span>{t("chat.chainCollapsed", { count: totalSteps })}</span>
      </div>
    );
  }

  return (
    <div className="thinkingChain">
      {localChain.map((group, idx) => (
        <ChainGroupItem
          key={group.iteration}
          group={group}
          isLast={idx === localChain.length - 1}
          streaming={streaming}
          onSkipStep={onSkipStep}
          onToggle={() => {
            setLocalChain(prev => prev.map((g, i) =>
              i === idx ? { ...g, collapsed: !g.collapsed } : g
            ));
          }}
        />
      ))}
    </div>
  );
}
