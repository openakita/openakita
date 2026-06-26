export type InboxPriority = "low" | "normal" | "high" | "critical" | string;

export type InboxMessage = {
  id: string;
  title: string;
  body_markdown: string;
  type: "notice" | "update" | "security" | "activity" | "tip" | string;
  priority: InboxPriority;
  cta?: {
    label?: string | null;
    url?: string | null;
    [key: string]: unknown;
  } | null;
  target_rule?: Record<string, unknown>;
  rollout_percent?: number;
  publish_at?: string | null;
  expire_at?: string | null;
  source?: string;
  raw?: Record<string, unknown>;
  received_at?: string;
  read_at?: string | null;
  clicked_at?: string | null;
  dismissed_at?: string | null;
};

export type InboxListResponse = {
  messages: InboxMessage[];
  unread_count: number;
};

export type InboxWsMessagePayload = {
  id?: string;
  title?: string;
  priority?: InboxPriority;
};

export type InboxUpdatePayload = {
  message_id?: string;
  title?: string;
  version?: string | null;
  manifest_url?: string | null;
  force_upgrade?: boolean;
  min_supported_version?: string | null;
  policy?: "prompt" | "forced_after_delay" | "forced_now" | string;
};

export function inboxPriorityRank(priority: InboxPriority | null | undefined): number {
  const value = String(priority || "").toLowerCase();
  if (value === "critical") return 4;
  if (value === "high") return 3;
  if (value === "normal") return 2;
  if (value === "low") return 1;
  return 0;
}

export function isHighPriorityInbox(priority: InboxPriority | null | undefined): boolean {
  return inboxPriorityRank(priority) >= 3;
}
