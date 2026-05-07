import type { ChatMcpCall } from "../utils/chatTypes";

export function MCPCallStrip({ calls }: { calls?: ChatMcpCall[] | null }) {
  if (!calls?.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, margin: "6px 0 8px" }}>
      {calls.map((call, i) => {
        const isError = (call.status || "").toLowerCase() === "error";
        const label = isError
          ? `MCP 调用失败：${call.server} · ${call.tool}`
          : `MCP 调用：${call.server} · ${call.tool}`;
        const tone = isError
          ? { color: "var(--danger)", bg: "rgba(239,68,68,0.06)" }
          : { color: "var(--muted)", bg: "rgba(124,58,237,0.06)" };
        return (
          <div
            key={`${call.server}/${call.tool}-${i}`}
            title={call.error || `${call.server} → ${call.tool}`}
            style={{
              border: "1px solid var(--line)",
              borderRadius: 8,
              padding: "5px 8px",
              fontSize: 12,
              display: "flex",
              gap: 8,
              alignItems: "center",
              color: tone.color,
              background: tone.bg,
            }}
          >
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {label}
              {call.auto_connected ? "（自动连接）" : ""}
              {call.reconnected ? "（自动重连后调用）" : ""}
            </span>
            {isError && call.error ? (
              <span style={{ opacity: 0.8, fontSize: 11, maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {call.error}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
