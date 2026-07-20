import { useTranslation } from "react-i18next";

import { Button } from "../../../components/ui/button";
import { IconAlertCircle, IconSend } from "../../../icons";
import type { ChatMessage, MessageCompletionAction } from "../utils/chatTypes";

export function MessageCompletionActions({
  msg,
  onAction,
}: {
  msg: ChatMessage;
  onAction?: (msg: ChatMessage, action: MessageCompletionAction) => void;
}) {
  const { t } = useTranslation();
  const actions = msg.completionActions ?? [];
  if (msg.streaming || msg.askUser || actions.length === 0 || !onAction) return null;

  return (
    <div className="mt-3 flex max-w-[min(100%,720px)] flex-wrap items-center gap-3 border-l-4 border-amber-500 bg-amber-500/10 px-4 py-3 text-sm">
      <IconAlertCircle size={20} className="shrink-0 text-amber-600 dark:text-amber-400" />
      <div className="min-w-[min(100%,220px)] flex-1">
        <div className="font-semibold text-foreground">
          {t("chat.diagnosticFeedbackTitle", "帮助我们进一步定位问题")}
        </div>
        <div className="mt-0.5 text-xs leading-5 text-muted-foreground">
          {t("chat.diagnosticFeedbackDesc", "提交本次反馈和近期日志，帮助开发者复现并修复问题。")}
        </div>
      </div>
      {actions.map((action, index) => (
        <Button
          key={`${action.type}-${index}`}
          type="button"
          size="sm"
          variant={action.style === "prominent" ? "default" : "outline"}
          className="shrink-0"
          onClick={() => onAction(msg, action)}
        >
          <IconSend size={15} />
          {t("chat.submitFeedbackLogs", "提交反馈日志")}
        </Button>
      ))}
    </div>
  );
}
