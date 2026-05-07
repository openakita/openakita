import { useEffect, useState } from "react";
import type { ChatSource } from "../utils/chatTypes";

function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    return `${u.hostname}${u.pathname === "/" ? "" : u.pathname}`;
  } catch {
    return url;
  }
}

function hostOf(source: ChatSource): string {
  if (source.hostname) return source.hostname;
  try {
    const u = new URL(source.final_url || source.requested_url);
    return u.hostname;
  } catch {
    return "";
  }
}

export type SourceStripProps = {
  sources?: ChatSource[] | null;
  conversationId?: string;
  httpApiBase?: () => string;
};

export function SourceStrip({ sources, conversationId, httpApiBase }: SourceStripProps) {
  const [blockedHosts, setBlockedHosts] = useState<Set<string>>(new Set());
  const [busyHost, setBusyHost] = useState<string | null>(null);

  const canManage = !!(conversationId && httpApiBase);

  useEffect(() => {
    if (!canManage) return;
    let cancelled = false;
    (async () => {
      try {
        const url = `${httpApiBase!()}/api/diagnostics/domain-rules?conversation_id=${encodeURIComponent(
          conversationId!,
        )}`;
        const resp = await fetch(url);
        if (!resp.ok) return;
        const body = (await resp.json()) as { blocked?: string[] };
        if (!cancelled && Array.isArray(body.blocked)) {
          setBlockedHosts(new Set(body.blocked));
        }
      } catch {
        // best-effort: leave the set empty
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canManage, conversationId, httpApiBase]);

  if (!sources?.length) return null;

  const toggleBlock = async (host: string, currentlyBlocked: boolean) => {
    if (!canManage || !host) return;
    setBusyHost(host);
    try {
      const path = currentlyBlocked
        ? "/api/diagnostics/domain-unblock"
        : "/api/diagnostics/domain-block";
      const resp = await fetch(
        `${httpApiBase!()}${path}?conversation_id=${encodeURIComponent(conversationId!)}&host=${encodeURIComponent(host)}`,
        { method: "POST" },
      );
      if (resp.ok) {
        const body = (await resp.json()) as { blocked?: string[] };
        if (Array.isArray(body.blocked)) setBlockedHosts(new Set(body.blocked));
      }
    } finally {
      setBusyHost(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, margin: "6px 0 8px" }}>
      {sources.map((source, i) => {
        const requested = source.requested_url || source.final_url;
        const finalUrl = source.final_url || requested;
        const isError = source.status === "error";
        const host = hostOf(source);
        const isBlocked = !!host && blockedHosts.has(host);
        const text = isError
          ? `链接未读取：${shortUrl(finalUrl)}`
          : source.redirected
            ? `已读取：${shortUrl(finalUrl)}（由 ${shortUrl(requested)} 跳转）`
            : `已读取：${shortUrl(finalUrl)}`;
        return (
          <div
            key={`${finalUrl}-${i}`}
            title={source.hint || `${requested} -> ${finalUrl}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              border: "1px solid var(--line)",
              borderRadius: 8,
              padding: "5px 8px",
              fontSize: 12,
              color: isError ? "var(--danger)" : "var(--muted)",
              background: isError
                ? "rgba(239,68,68,0.06)"
                : isBlocked
                  ? "rgba(251,191,36,0.08)"
                  : "rgba(37,99,235,0.04)",
            }}
          >
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {text}
              {source.from_cache ? "（本轮复用已读取结果）" : ""}
              {isBlocked ? "（已屏蔽）" : ""}
              {source.hint ? <span style={{ marginLeft: 6, opacity: 0.75 }}>{source.hint}</span> : null}
            </span>
            {canManage && host ? (
              <button
                type="button"
                onClick={() => toggleBlock(host, isBlocked)}
                disabled={busyHost === host}
                style={{
                  fontSize: 11,
                  padding: "2px 8px",
                  borderRadius: 6,
                  border: "1px solid var(--line)",
                  background: "transparent",
                  cursor: busyHost === host ? "wait" : "pointer",
                  color: isBlocked ? "var(--accent)" : "var(--muted)",
                  whiteSpace: "nowrap",
                }}
                title={
                  isBlocked
                    ? `在本会话取消屏蔽 ${host}`
                    : `在本会话屏蔽 ${host}（不影响其他对话）`
                }
              >
                {isBlocked ? "取消屏蔽" : "屏蔽该域名"}
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
