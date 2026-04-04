import { toast } from "sonner";
import { copyToClipboard } from "./clipboard";

export function notifySuccess(msg: string) {
  toast.success(msg, { duration: 4000 });
}

export function notifyError(msg: string) {
  toast.error(msg, {
    duration: 8000,
    action: {
      label: "复制",
      onClick: () => copyToClipboard(msg),
    },
  });
}

export function notifyInfo(msg: string, duration = 5000) {
  toast.info(msg, { duration });
}

export function notifyWarning(msg: string, duration = 6000) {
  toast.warning(msg, { duration });
}

export function notifyLoading(msg: string): string | number {
  return toast.loading(msg);
}

export function dismissLoading(id: string | number) {
  toast.dismiss(id);
}

const _recentNotifications = new Map<string, number>();
const DEDUPE_WINDOW_MS = 5000;
const DEDUPE_MAX_ENTRIES = 100;

export function notifyOnce(key: string, level: "info" | "warning" | "error", msg: string) {
  const now = Date.now();
  const last = _recentNotifications.get(key);
  if (last && now - last < DEDUPE_WINDOW_MS) return;
  _recentNotifications.set(key, now);

  if (_recentNotifications.size > DEDUPE_MAX_ENTRIES) {
    for (const [k, ts] of _recentNotifications) {
      if (now - ts > DEDUPE_WINDOW_MS) _recentNotifications.delete(k);
    }
  }

  if (level === "error") notifyError(msg);
  else if (level === "warning") notifyWarning(msg);
  else notifyInfo(msg);
}
