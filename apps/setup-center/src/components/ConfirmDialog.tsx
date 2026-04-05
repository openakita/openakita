import { useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from "@/components/ui/alert-dialog";
import type { ConfirmDialogState } from "@/hooks/useNotifications";

type ConfirmDialogProps = {
  dialog: ConfirmDialogState | null;
  onClose: () => void;
};

export function ConfirmDialog({ dialog, onClose }: ConfirmDialogProps) {
  const { t } = useTranslation();
  const lastDialog = useRef(dialog);
  if (dialog) lastDialog.current = dialog;

  const snapshot = lastDialog.current;

  return (
    <AlertDialog open={!!dialog} onOpenChange={(open) => { if (!open) onClose(); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{snapshot?.title || t("common.confirmTitle", { defaultValue: "确认操作" })}</AlertDialogTitle>
          <AlertDialogDescription className="whitespace-pre-wrap">
            {snapshot?.message}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{snapshot?.cancelLabel || t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant={snapshot?.destructive !== false ? "destructive" : "default"}
            onClick={() => { snapshot?.onConfirm(); onClose(); }}
          >
            {snapshot?.confirmLabel || t("common.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
