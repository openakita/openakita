import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ChatTodo, ChatTodoStep } from "../utils/chatTypes";
import {
  IconClipboard, IconChevronDown, IconPlay, IconCheck,
  IconCircle, IconMinus, IconX,
} from "../../../icons";

function FloatingTodoStepItem({ step, idx }: { step: ChatTodoStep; idx: number }) {
  const icon =
    step.status === "completed" ? <IconCheck size={13} /> :
    step.status === "in_progress" ? <IconPlay size={11} /> :
    step.status === "skipped" ? <IconMinus size={13} /> :
    step.status === "cancelled" ? <IconX size={13} /> :
    step.status === "failed" ? <IconX size={13} /> :
    <IconCircle size={9} />;
  const color =
    step.status === "completed" ? "rgba(16,185,129,1)"
    : step.status === "in_progress" ? "var(--brand)"
    : step.status === "failed" ? "rgba(239,68,68,1)"
    : step.status === "cancelled" ? "var(--muted)"
    : step.status === "skipped" ? "var(--muted)" : "var(--muted)";
  const descText = typeof step.description === "string" ? step.description : JSON.stringify(step.description);
  const resultText = step.result
    ? (typeof step.result === "string" ? step.result : JSON.stringify(step.result))
    : null;
  return (
    <div className={`floatingTodoStepRow ${step.status === "in_progress" ? "floatingTodoStepActive" : ""}`}>
      <span className="floatingTodoStepIcon" style={{ color }}>{icon}</span>
      <div className="floatingTodoStepContent">
        <span style={{ opacity: step.status === "skipped" || step.status === "cancelled" ? 0.5 : 1 }}>{idx + 1}. {descText}</span>
        {resultText && <div className="floatingTodoStepResult">{resultText}</div>}
      </div>
    </div>
  );
}

export function FloatingPlanBar({ plan }: { plan: ChatTodo }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const completed = plan.steps.filter((s) => s.status === "completed").length;
  const total = plan.steps.length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  const allDone = completed === total && total > 0;

  const activeStep = plan.steps.find((s) => s.status === "in_progress")
    || plan.steps.find((s) => s.status === "pending");
  const activeIdx = activeStep ? plan.steps.indexOf(activeStep) : -1;
  const activeDesc = activeStep
    ? (typeof activeStep.description === "string" ? activeStep.description : JSON.stringify(activeStep.description))
    : null;

  return (
    <div className="floatingTodoBar">
      <div className="floatingTodoHeader" onClick={() => setExpanded((v) => !v)}>
        <div className="floatingTodoHeaderLeft">
          <IconClipboard size={14} style={{ opacity: 0.6 }} />
          <span className="floatingTodoTitle">
            {typeof plan.taskSummary === "string" ? plan.taskSummary : JSON.stringify(plan.taskSummary)}
          </span>
        </div>
        <div className="floatingTodoHeaderRight">
          <span className="floatingTodoProgress">{completed}/{total}</span>
          <span className="floatingTodoChevron" style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}>
            <IconChevronDown size={14} />
          </span>
        </div>
      </div>

      <div className="floatingTodoProgressBar">
        <div className="floatingTodoProgressFill" style={{ width: `${pct}%` }} />
      </div>

      {!expanded && activeStep && !allDone && (
        <div className="floatingTodoActive">
          <span className="floatingTodoActiveIcon"><IconPlay size={11} /></span>
          <span className="floatingTodoActiveText">{activeIdx + 1}/{total} {activeDesc}</span>
        </div>
      )}
      {!expanded && allDone && (
        <div className="floatingTodoActive floatingTodoDone">
          <span className="floatingTodoActiveIcon"><IconCheck size={12} /></span>
          <span className="floatingTodoActiveText">{t("chat.allDone", "全部完成")}</span>
        </div>
      )}

      {expanded && (
        <div className="floatingTodoSteps">
          {plan.steps.map((step, idx) => (
            <FloatingTodoStepItem key={step.id || idx} step={step} idx={idx} />
          ))}
        </div>
      )}
    </div>
  );
}
