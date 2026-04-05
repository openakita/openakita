import { useCallback, useState } from "react";

export type ConfirmDialogState = {
  message: string;
  onConfirm: () => void;
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
};

export function useNotifications() {
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState | null>(null);

  const askConfirm = useCallback((message: string, onConfirm: () => void) => {
    setConfirmDialog({ message, onConfirm });
  }, []);

  return {
    confirmDialog, setConfirmDialog,
    askConfirm,
  };
}
