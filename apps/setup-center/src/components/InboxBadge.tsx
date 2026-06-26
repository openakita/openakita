import { useCallback, useEffect, useState } from "react";
import { safeFetch } from "../providers";

export const INBOX_UNREAD_CHANGED_EVENT = "openakita:inbox-unread-changed";
export const INBOX_REFRESH_EVENT = "openakita:inbox-refresh";

type InboxBadgeProps = {
  apiBaseUrl: string;
  serviceRunning: boolean;
  countOverride?: number | null;
};

type InboxRefreshDetail = {
  unreadCount?: number;
};

export function InboxBadge({ apiBaseUrl, serviceRunning, countOverride }: InboxBadgeProps) {
  const [count, setCount] = useState(0);

  const fetchUnread = useCallback(async () => {
    if (!serviceRunning) {
      setCount(0);
      return;
    }
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/inbox/unread-count`, {
        signal: AbortSignal.timeout(3_000),
      });
      const data = await resp.json();
      const next = Math.max(0, Number(data?.unread_count || 0));
      setCount(next);
      window.dispatchEvent(
        new CustomEvent<InboxRefreshDetail>(INBOX_UNREAD_CHANGED_EVENT, {
          detail: { unreadCount: next },
        }),
      );
    } catch {
      setCount(0);
    }
  }, [apiBaseUrl, serviceRunning]);

  useEffect(() => {
    if (typeof countOverride === "number") {
      setCount(Math.max(0, countOverride));
      return;
    }
    void fetchUnread();
  }, [countOverride, fetchUnread]);

  useEffect(() => {
    const onRefresh = (event: Event) => {
      const detail = (event as CustomEvent<InboxRefreshDetail>).detail;
      if (typeof detail?.unreadCount === "number") {
        setCount(Math.max(0, detail.unreadCount));
        return;
      }
      void fetchUnread();
    };
    window.addEventListener(INBOX_REFRESH_EVENT, onRefresh);
    window.addEventListener(INBOX_UNREAD_CHANGED_EVENT, onRefresh);
    return () => {
      window.removeEventListener(INBOX_REFRESH_EVENT, onRefresh);
      window.removeEventListener(INBOX_UNREAD_CHANGED_EVENT, onRefresh);
    };
  }, [fetchUnread]);

  if (count <= 0) return null;

  return (
    <span className="navBadge" aria-label={`${count} unread inbox messages`}>
      {count > 99 ? "99+" : count}
    </span>
  );
}
