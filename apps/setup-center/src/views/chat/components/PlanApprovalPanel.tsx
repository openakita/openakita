import { useState } from "react";
import { useTranslation } from "react-i18next";
import { IconCheck, IconX, IconEdit } from "../../../icons";
import type { PlanApprovalEvent, ChatTodo } from "../../../types";

interface PlanApprovalPanelProps {
  approval: PlanApprovalEvent;
  plan: ChatTodo | null;
  onApprove: () => void;
  onReject: (feedback: string) => void;
  onDismiss: () => void;
}

export function PlanApprovalPanel({
  approval, plan, onApprove, onReject, onDismiss,
}: PlanApprovalPanelProps) {
  const { t } = useTranslation();
  const [feedback, setFeedback] = useState("");
  const [showFeedback, setShowFeedback] = useState(false);

  return (
    <div className="planApprovalPanel">
      <div className="planApprovalHeader">
        <span className="planApprovalTitle">
          {t("chat.planReady", "计划已就绪，是否开始执行？")}
        </span>
        <button className="planApprovalDismiss" onClick={onDismiss} title={t("common.close", "关闭")}>
          <IconX size={14} />
        </button>
      </div>

      {approval.summary && (
        <div className="planApprovalSummary">{approval.summary}</div>
      )}

      {plan && plan.steps.length > 0 && (
        <div className="planApprovalSteps">
          {plan.steps.map((step, idx) => (
            <div key={step.id || idx} className="planApprovalStep">
              {idx + 1}. {step.description}
            </div>
          ))}
        </div>
      )}

      {showFeedback ? (
        <div className="planApprovalFeedback">
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder={t("chat.planFeedbackPlaceholder", "请输入修改意见...")}
            rows={3}
            autoFocus
          />
          <div className="planApprovalFeedbackActions">
            <button
              className="planApprovalApprove"
              onClick={() => { onReject(feedback); setShowFeedback(false); setFeedback(""); }}
            >
              {t("chat.submitFeedback", "提交修改意见")}
            </button>
            <button onClick={() => setShowFeedback(false)}>
              {t("common.cancel", "取消")}
            </button>
          </div>
        </div>
      ) : (
        <div className="planApprovalActions">
          <button className="planApprovalApprove" onClick={onApprove}>
            <IconCheck size={14} />
            {t("chat.approvePlan", "批准执行")}
          </button>
          <button className="planApprovalReject" onClick={() => setShowFeedback(true)}>
            <IconEdit size={14} />
            {t("chat.rejectPlan", "修改计划")}
          </button>
          <button className="planApprovalReject" onClick={onDismiss}>
            <IconX size={14} />
            {t("common.cancel", "取消")}
          </button>
        </div>
      )}
    </div>
  );
}
