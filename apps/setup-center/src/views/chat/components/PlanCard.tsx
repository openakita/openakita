import { useState } from "react";
import type * as React from "react";
import { useTranslation } from "react-i18next";
import {
  IconClipboard,
  IconCheck,
  IconCircle,
  IconPlay,
  IconMinus,
  IconX,
} from "../../../icons";
import type { ChatTodo, ChatTodoStep } from "../utils/chatTypes";

const RESULT_PREVIEW_CHARS = 200;

const STATUS_PALETTE: Record<
  ChatTodoStep["status"],
  { color: string; bg: string; ring: string; label: string }
> = {
  completed: {
    color: "rgba(16,185,129,1)",
    bg: "rgba(16,185,129,0.08)",
    ring: "rgba(16,185,129,0.35)",
    label: "已完成",
  },
  in_progress: {
    color: "var(--brand)",
    bg: "rgba(37,99,235,0.10)",
    ring: "rgba(37,99,235,0.45)",
    label: "进行中",
  },
  pending: {
    color: "var(--muted)",
    bg: "transparent",
    ring: "var(--line)",
    label: "待办",
  },
  skipped: {
    color: "var(--muted)",
    bg: "transparent",
    ring: "var(--line)",
    label: "已跳过",
  },
  failed: {
    color: "rgba(239,68,68,1)",
    bg: "rgba(239,68,68,0.08)",
    ring: "rgba(239,68,68,0.4)",
    label: "失败",
  },
  cancelled: {
    color: "var(--muted)",
    bg: "transparent",
    ring: "var(--line)",
    label: "已取消",
  },
};

function StepIcon({ status }: { status: ChatTodoStep["status"] }) {
  switch (status) {
    case "completed":
      return <IconCheck size={13} />;
    case "in_progress":
      return <IconPlay size={11} />;
    case "skipped":
    case "cancelled":
      return <IconMinus size={13} />;
    case "failed":
      return <IconX size={13} />;
    default:
      return <IconCircle size={9} />;
  }
}

function planStateBadge(plan: ChatTodo): { color: string; bg: string; label: string } {
  switch (plan.status) {
    case "completed":
      return { color: "rgba(16,185,129,1)", bg: "rgba(16,185,129,0.12)", label: "全部完成" };
    case "failed":
      return { color: "rgba(239,68,68,1)", bg: "rgba(239,68,68,0.12)", label: "已失败" };
    case "cancelled":
      return { color: "var(--muted)", bg: "rgba(0,0,0,0.04)", label: "已取消" };
    default:
      return { color: "var(--brand)", bg: "rgba(37,99,235,0.10)", label: "进行中" };
  }
}

export type PlanStepAction = "skip" | "retry";

export function PlanCard({
  plan,
  onStepAction,
}: {
  plan: ChatTodo;
  onStepAction?: (action: PlanStepAction, stepIdx: number, description: string) => void;
}) {
  const { t } = useTranslation();
  const total = plan.steps.length;
  const completed = plan.steps.filter((s) => s.status === "completed").length;
  const failedCount = plan.steps.filter((s) => s.status === "failed").length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  const badge = planStateBadge(plan);

  const summaryText =
    typeof plan.taskSummary === "string" ? plan.taskSummary : JSON.stringify(plan.taskSummary);

  return (
    <div
      style={{
        marginTop: 6,
        marginBottom: 8,
        border: "1px solid var(--line)",
        borderRadius: 12,
        padding: 12,
        background: "var(--panel2, rgba(0,0,0,0.02))",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <IconClipboard size={14} style={{ opacity: 0.6 }} />
        <div style={{ fontWeight: 600, fontSize: 13, flex: 1, minWidth: 0 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {summaryText || t("chat.plan", "执行计划")}
          </span>
        </div>
        <span
          style={{
            fontSize: 11,
            padding: "2px 8px",
            borderRadius: 999,
            color: badge.color,
            background: badge.bg,
            border: `1px solid ${badge.color}33`,
            whiteSpace: "nowrap",
          }}
        >
          {badge.label}
        </span>
        <span style={{ fontSize: 11, color: "var(--muted)", whiteSpace: "nowrap" }}>
          {completed}/{total}
        </span>
      </div>

      <div
        aria-hidden
        style={{
          height: 4,
          borderRadius: 999,
          background: "var(--line)",
          overflow: "hidden",
          marginBottom: 10,
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background:
              failedCount > 0 ? "rgba(239,68,68,0.85)" : "var(--brand, rgba(37,99,235,0.9))",
            transition: "width 0.3s ease",
          }}
        />
      </div>

      <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        {plan.steps.map((step, idx) => (
          <PlanStepRow
            key={step.id || idx}
            step={step}
            idx={idx}
            onStepAction={onStepAction}
          />
        ))}
      </ol>
    </div>
  );
}

function PlanStepRow({
  step,
  idx,
  onStepAction,
}: {
  step: ChatTodoStep;
  idx: number;
  onStepAction?: (action: PlanStepAction, stepIdx: number, description: string) => void;
}) {
  const { t } = useTranslation();
  const palette = STATUS_PALETTE[step.status] || STATUS_PALETTE.pending;
  const isActive = step.status === "in_progress";
  const desc =
    typeof step.description === "string"
      ? step.description
      : JSON.stringify(step.description);
  const result = step.result
    ? typeof step.result === "string"
      ? step.result
      : JSON.stringify(step.result)
    : null;
  const isLong = !!result && result.length > RESULT_PREVIEW_CHARS;
  const [expanded, setExpanded] = useState(step.status === "failed" && isLong);
  const [copied, setCopied] = useState(false);
  const visibleResult =
    result && isLong && !expanded ? result.slice(0, RESULT_PREVIEW_CHARS) + "…" : result;

  const handleCopy = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard may be denied; fail silently — user can manually select text
    }
  };

  return (
    <li
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "6px 8px",
        borderRadius: 8,
        background: isActive ? palette.bg : "transparent",
        border: `1px solid ${isActive ? palette.ring : "transparent"}`,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 18,
          height: 18,
          borderRadius: "50%",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          background: palette.bg || "var(--line)",
          color: palette.color,
          border: `1px solid ${palette.ring}`,
          flexShrink: 0,
          marginTop: 1,
        }}
      >
        <StepIcon status={step.status} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 12.5,
            color:
              step.status === "skipped" || step.status === "cancelled"
                ? "var(--muted)"
                : step.status === "failed"
                  ? palette.color
                  : "var(--fg, var(--text))",
            fontWeight: isActive ? 600 : 400,
            textDecoration:
              step.status === "skipped" || step.status === "cancelled"
                ? "line-through"
                : "none",
            lineHeight: 1.4,
          }}
        >
          <span style={{ opacity: 0.55, marginRight: 6 }}>{idx + 1}.</span>
          {desc}
          <span
            style={{
              marginLeft: 8,
              fontSize: 10,
              color: palette.color,
              opacity: 0.85,
            }}
          >
            {palette.label}
          </span>
        </div>
        {visibleResult && (
          <div
            style={{
              marginTop: 4,
              fontSize: 11.5,
              color: "var(--muted)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              borderLeft: `2px solid ${palette.ring}`,
              paddingLeft: 8,
              maxHeight: expanded ? 360 : undefined,
              overflowY: expanded && isLong ? "auto" : "visible",
            }}
          >
            {visibleResult}
          </div>
        )}
        {(result || (onStepAction && (isActive || step.status === "failed"))) && (
          <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
            {isLong && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                style={planActionBtnStyle}
              >
                {expanded
                  ? t("chat.plan.collapseResult", { defaultValue: "收起" })
                  : t("chat.plan.expandResult", { defaultValue: "展开完整结果" })}
              </button>
            )}
            {result && (
              <button
                type="button"
                onClick={handleCopy}
                style={planActionBtnStyle}
                title={t("chat.plan.copyHint", { defaultValue: "复制完整结果到剪贴板" }) as string}
              >
                {copied
                  ? t("chat.plan.copied", { defaultValue: "已复制" })
                  : t("chat.plan.copy", { defaultValue: "复制" })}
              </button>
            )}
            {onStepAction && isActive && (
              <button
                type="button"
                onClick={() => onStepAction("skip", idx, desc)}
                title={t("chat.plan.skipHint", {
                  defaultValue: "在输入框生成「跳过此步」的请求",
                }) as string}
                style={planActionBtnStyle}
              >
                {t("chat.plan.skip", { defaultValue: "跳过此步" })}
              </button>
            )}
            {onStepAction && step.status === "failed" && (
              <button
                type="button"
                onClick={() => onStepAction("retry", idx, desc)}
                title={t("chat.plan.retryHint", {
                  defaultValue: "在输入框生成「重试此步」的请求",
                }) as string}
                style={planActionBtnStyle}
              >
                {t("chat.plan.retry", { defaultValue: "重试此步" })}
              </button>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

const planActionBtnStyle: React.CSSProperties = {
  fontSize: 11,
  padding: "2px 8px",
  borderRadius: 6,
  border: "1px solid var(--line)",
  background: "transparent",
  cursor: "pointer",
  color: "var(--muted)",
};
