import { useState, useEffect, useCallback, useRef } from "react";
import { safeFetch } from "../providers";
import { IconX } from "../icons";

function fmtShortDate(v: string | null | undefined): string {
  if (!v) return "";
  const d = new Date(v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

type InboxMsg = {
  id: string;
  org_id: string;
  org_name: string;
  priority: string;
  title: string;
  body: string;
  source_node: string | null;
  category: string;
  requires_approval: boolean;
  approval_options: string[];
  approval_id: string | null;
  status: string;
  created_at: string;
  acted_at: string | null;
  acted_result: string | null;
  acted_by: string | null;
};

type InboxResponse = {
  messages: InboxMsg[];
  unread_count: number;
  pending_approvals: number;
};

const PRIORITY_COLORS: Record<string, string> = {
  alert: "#ef4444",
  approval: "#f59e0b",
  action: "#3b82f6",
  warning: "#f97316",
  notice: "#8b5cf6",
  info: "#6b7280",
};

const PRIORITY_LABELS: Record<string, string> = {
  alert: "紧急",
  approval: "待审批",
  action: "待处理",
  warning: "警告",
  notice: "通知",
  info: "消息",
};

const CATEGORY_LABELS: Record<string, string> = {
  general: "通用",
  task_complete: "任务完成",
  approval: "审批",
  progress: "进度",
  warning: "警告",
  scaling: "扩编",
};

export function OrgInboxSidebar({
  apiBaseUrl,
  orgId,
  visible,
  onClose,
}: {
  apiBaseUrl: string;
  orgId: string;
  visible: boolean;
  onClose: () => void;
}) {
  const [messages, setMessages] = useState<InboxMsg[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [pendingApprovals, setPendingApprovals] = useState(0);
  const [filter, setFilter] = useState<"all" | "unread" | "approval">("all");
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchInbox = useCallback(async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filter === "unread") params.set("unread_only", "true");
      if (filter === "approval") params.set("pending_approval", "true");
      params.set("limit", "50");

      const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/inbox?${params}`);
      if (resp.ok) {
        const data: InboxResponse = await resp.json();
        setMessages(data.messages);
        setUnreadCount(data.unread_count);
        setPendingApprovals(data.pending_approvals);
      }
    } catch (e) {
      console.error("Failed to fetch inbox", e);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, orgId, filter]);

  useEffect(() => {
    if (visible && orgId) {
      fetchInbox();
      pollRef.current = setInterval(fetchInbox, 10000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [visible, orgId, fetchInbox]);

  const handleMarkRead = async (msgId: string) => {
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/inbox/${msgId}/read`, { method: "POST" });
      if (!resp.ok) console.error("Mark read failed:", resp.status);
    } catch (e) {
      console.error("Mark read error:", e);
    }
    fetchInbox();
  };

  const handleMarkAllRead = async () => {
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/inbox/read-all`, { method: "POST" });
      if (!resp.ok) console.error("Mark all read failed:", resp.status);
    } catch (e) {
      console.error("Mark all read error:", e);
    }
    fetchInbox();
  };

  const handleResolve = async (msgId: string, decision: string) => {
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/inbox/${msgId}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      if (!resp.ok) console.error("Resolve failed:", resp.status);
    } catch (e) {
      console.error("Resolve error:", e);
    }
    fetchInbox();
  };

  if (!visible) return null;

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      height: "100%",
      background: "var(--card-bg, var(--bg-app, #fff))",
    }}>
      {/* Header */}
      <div style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--border, #e5e7eb)",
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}>
        <span style={{ fontWeight: 600, fontSize: 15, flex: 1 }}>
          组织消息
          {unreadCount > 0 && (
            <span style={{
              marginLeft: 6,
              background: "#ef4444",
              color: "#fff",
              borderRadius: 10,
              padding: "1px 7px",
              fontSize: 11,
              fontWeight: 500,
            }}>{unreadCount}</span>
          )}
          {pendingApprovals > 0 && (
            <span style={{
              marginLeft: 4,
              background: "#f59e0b",
              color: "#fff",
              borderRadius: 10,
              padding: "1px 7px",
              fontSize: 11,
              fontWeight: 500,
            }}>{pendingApprovals} 待审</span>
          )}
        </span>
        <button
          onClick={handleMarkAllRead}
          style={{
            border: "none",
            background: "none",
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-secondary, #6b7280)",
            minHeight: 36,
            minWidth: 44,
            padding: "6px 8px",
          }}
          title="全部已读"
        >全部已读</button>
        <button
          onClick={onClose}
          style={{
            border: "none",
            background: "none",
            cursor: "pointer",
            fontSize: 18,
            lineHeight: 1,
            color: "var(--text-secondary, #6b7280)",
            minHeight: 36,
            minWidth: 36,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        ><IconX size={16} /></button>
      </div>

      {/* Filters */}
      <div style={{
        padding: "8px 16px",
        display: "flex",
        gap: 6,
        borderBottom: "1px solid var(--border, #e5e7eb)",
      }}>
        {(["all", "unread", "approval"] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid " + (filter === f ? "var(--accent, #3b82f6)" : "var(--border, #e5e7eb)"),
              background: filter === f ? "var(--accent, #3b82f6)" : "transparent",
              color: filter === f ? "#fff" : "var(--text, #374151)",
              fontSize: 12,
              cursor: "pointer",
              minHeight: 36,
            }}
          >
            {f === "all" ? "全部" : f === "unread" ? "未读" : "待审批"}
          </button>
        ))}
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
        {loading && messages.length === 0 && (
          <div style={{ textAlign: "center", padding: 20, color: "#9ca3af", fontSize: 13 }}>
            加载中...
          </div>
        )}
        {!loading && messages.length === 0 && (
          <div style={{ textAlign: "center", padding: 20, color: "#9ca3af", fontSize: 13 }}>
            暂无消息
          </div>
        )}
        {messages.map(msg => {
          const isExpanded = expandedId === msg.id;
          const isUnread = msg.status === "unread";
          const isActed = msg.status === "acted";
          const prColor = PRIORITY_COLORS[msg.priority] || "#6b7280";

          return (
            <div
              key={msg.id}
              onClick={() => {
                setExpandedId(isExpanded ? null : msg.id);
                if (isUnread) handleMarkRead(msg.id);
              }}
              style={{
                padding: "10px 16px",
                cursor: "pointer",
                borderBottom: "1px solid var(--border-light, #f3f4f6)",
                background: isUnread ? "var(--unread-bg, #eff6ff)" : "transparent",
                opacity: isActed ? 0.7 : 1,
              }}
            >
              {/* Top row */}
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <span style={{
                  display: "inline-block",
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: prColor,
                  flexShrink: 0,
                }} />
                <span style={{
                  fontSize: 11,
                  color: prColor,
                  fontWeight: 500,
                }}>{PRIORITY_LABELS[msg.priority] || msg.priority}</span>
                {msg.category !== "general" && (
                  <span style={{
                    fontSize: 10,
                    color: "#9ca3af",
                    background: "#f3f4f6",
                    borderRadius: 4,
                    padding: "1px 5px",
                  }}>{CATEGORY_LABELS[msg.category] || msg.category}</span>
                )}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 10, color: "#9ca3af" }}>
                  {fmtShortDate(msg.created_at)}
                </span>
              </div>

              {/* Title */}
              <div style={{
                fontSize: 13,
                fontWeight: isUnread ? 600 : 400,
                color: "var(--text, #374151)",
                marginBottom: isExpanded ? 6 : 0,
              }}>
                {msg.title}
                {isActed && msg.acted_result && (
                  <span style={{
                    marginLeft: 6,
                    fontSize: 11,
                    color: msg.acted_result === "approve" ? "#22c55e" : "#ef4444",
                  }}>
                    [{msg.acted_result === "approve" ? "已批准" : "已拒绝"}]
                  </span>
                )}
              </div>

              {/* Expanded body */}
              {isExpanded && (
                <div style={{ marginTop: 4 }}>
                  <div style={{
                    fontSize: 12,
                    color: "var(--text-secondary, #6b7280)",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.5,
                    maxHeight: 200,
                    overflowY: "auto",
                  }}>{msg.body}</div>

                  {msg.source_node && (
                    <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 4 }}>
                      来源: {msg.source_node}
                    </div>
                  )}

                  {msg.approval_id && (
                    <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 2 }}>
                      审批编号: {msg.approval_id}
                    </div>
                  )}

                  {msg.requires_approval && !isActed && (
                    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleResolve(msg.id, "approve"); }}
                        style={{
                          padding: "8px 20px",
                          borderRadius: 6,
                          border: "none",
                          background: "#22c55e",
                          color: "#fff",
                          fontSize: 13,
                          cursor: "pointer",
                          minHeight: 36,
                        }}
                      >批准</button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleResolve(msg.id, "reject"); }}
                        style={{
                          padding: "8px 20px",
                          borderRadius: 6,
                          border: "none",
                          background: "#ef4444",
                          color: "#fff",
                          fontSize: 13,
                          cursor: "pointer",
                          minHeight: 36,
                        }}
                      >拒绝</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
