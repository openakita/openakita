// ─── TokenStatsView: Token 用量统计面板 ───
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

type PeriodKey = "1d" | "3d" | "1w" | "1m" | "6m" | "1y";

type SummaryRow = {
  group_key: string;
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cache_creation: number;
  total_cache_read: number;
  request_count: number;
};

type TimelineRow = {
  time_bucket: string;
  total_input: number;
  total_output: number;
  total_tokens: number;
  request_count: number;
};

type TotalRow = {
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cache_creation: number;
  total_cache_read: number;
  request_count: number;
};

type SessionRow = {
  session_id: string;
  first_call: string;
  last_call: string;
  total_input: number;
  total_output: number;
  total_tokens: number;
  request_count: number;
  operation_types: string;
  endpoints: string;
};

const PERIODS: { key: PeriodKey; label: string }[] = [
  { key: "1d", label: "1天" },
  { key: "3d", label: "3天" },
  { key: "1w", label: "1周" },
  { key: "1m", label: "1月" },
  { key: "6m", label: "半年" },
  { key: "1y", label: "1年" },
];

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function MiniBar({ value, max, color = "var(--brand)" }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min(value / max, 1) * 100 : 0;
  return (
    <div style={{ width: "100%", height: 6, background: "var(--bg-secondary)", borderRadius: 3, overflow: "hidden" }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3, transition: "width 0.3s" }} />
    </div>
  );
}

export function TokenStatsView({
  serviceRunning,
  apiBaseUrl = "http://127.0.0.1:18900",
}: {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}) {
  const { t } = useTranslation();
  const [period, setPeriod] = useState<PeriodKey>("1d");
  const [total, setTotal] = useState<TotalRow | null>(null);
  const [byEndpoint, setByEndpoint] = useState<SummaryRow[]>([]);
  const [byOp, setByOp] = useState<SummaryRow[]>([]);
  const [timeline, setTimeline] = useState<TimelineRow[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchAll = useCallback(async () => {
    if (!serviceRunning) return;
    setLoading(true);
    try {
      const base = `${apiBaseUrl}/api/stats/tokens`;
      const [totalRes, epRes, opRes, tlRes, sessRes] = await Promise.all([
        fetch(`${base}/total?period=${period}`),
        fetch(`${base}/summary?period=${period}&group_by=endpoint_name`),
        fetch(`${base}/summary?period=${period}&group_by=operation_type`),
        fetch(`${base}/timeline?period=${period}&interval=${period === "1d" ? "hour" : "day"}`),
        fetch(`${base}/sessions?period=${period}&limit=20`),
      ]);
      const [totalJ, epJ, opJ, tlJ, sessJ] = await Promise.all([
        totalRes.json(), epRes.json(), opRes.json(), tlRes.json(), sessRes.json(),
      ]);
      setTotal(totalJ.data || null);
      setByEndpoint(epJ.data || []);
      setByOp(opJ.data || []);
      setTimeline(tlJ.data || []);
      setSessions(sessJ.data || []);
    } catch (e) {
      console.warn("TokenStats fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [serviceRunning, apiBaseUrl, period]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const maxTl = Math.max(...timeline.map((r) => r.total_tokens), 1);

  if (!serviceRunning) {
    return (
      <div className="card" style={{ textAlign: "center", padding: 40, opacity: 0.5 }}>
        {t("tokenStats.serviceNotRunning", "服务未运行，无法查看统计")}
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 960, margin: "0 auto" }}>
      <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>
        {t("tokenStats.title", "Token 用量统计")}
      </h2>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 16, lineHeight: 1.6 }}>
        {t("tokenStats.disclaimer", "⚠ 本地 token 计算与服务商算法无法保证完全一致，实际用量以服务商账单为准，此处统计仅供参考。")}
      </div>

      {/* Period selector */}
      <div style={{ display: "flex", gap: 6, marginBottom: 20, flexWrap: "wrap" }}>
        {PERIODS.map((p) => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            style={{
              padding: "4px 14px", borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: "pointer",
              border: period === p.key ? "1.5px solid var(--brand)" : "1px solid var(--line)",
              background: period === p.key ? "var(--brand-bg)" : "var(--bg)",
              color: period === p.key ? "var(--brand)" : "var(--text-secondary)",
            }}
          >
            {p.label}
          </button>
        ))}
        <button onClick={fetchAll} disabled={loading} style={{
          padding: "4px 14px", borderRadius: 6, fontSize: 12, border: "1px solid var(--line)",
          background: "var(--bg)", cursor: "pointer", opacity: loading ? 0.5 : 1,
        }}>
          {loading ? "..." : t("tokenStats.refresh", "刷新")}
        </button>
      </div>

      {/* Summary cards */}
      {total && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginBottom: 24 }}>
          {[
            { label: t("tokenStats.totalTokens", "总 Token"), value: total.total_tokens, color: "var(--brand)" },
            { label: t("tokenStats.inputTokens", "输入"), value: total.total_input, color: "#3b82f6" },
            { label: t("tokenStats.outputTokens", "输出"), value: total.total_output, color: "#10b981" },
            { label: t("tokenStats.requests", "请求数"), value: total.request_count, color: "#8b5cf6" },
          ].map((card) => (
            <div key={card.label} style={{
              padding: "14px 16px", borderRadius: 10, border: "1px solid var(--line)",
              background: "var(--bg)",
            }}>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{card.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: card.color }}>{fmtNum(card.value)}</div>
            </div>
          ))}
        </div>
      )}

      {/* Timeline bar chart */}
      {timeline.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
            {t("tokenStats.timeline", "时间线")}
          </h3>
          <div style={{
            display: "flex", alignItems: "flex-end", gap: 2, height: 100,
            padding: "0 4px", background: "var(--bg-secondary)", borderRadius: 8,
          }}>
            {timeline.map((r, i) => {
              const h = (r.total_tokens / maxTl) * 90;
              const inH = (r.total_input / maxTl) * 90;
              return (
                <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center", height: "100%" }}
                  title={`${r.time_bucket}\nInput: ${fmtNum(r.total_input)}\nOutput: ${fmtNum(r.total_output)}\nTotal: ${fmtNum(r.total_tokens)}`}
                >
                  <div style={{ width: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end" }}>
                    <div style={{ height: Math.max(h - inH, 1), background: "#10b981", borderRadius: "2px 2px 0 0", minWidth: 3 }} />
                    <div style={{ height: Math.max(inH, 1), background: "#3b82f6", borderRadius: "0 0 2px 2px", minWidth: 3 }} />
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--text-secondary)", marginTop: 2, padding: "0 4px" }}>
            <span>{timeline[0]?.time_bucket || ""}</span>
            <span>{timeline[timeline.length - 1]?.time_bucket || ""}</span>
          </div>
          <div style={{ display: "flex", gap: 12, fontSize: 10, marginTop: 4, color: "var(--text-secondary)" }}>
            <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "#3b82f6", marginRight: 3 }} />Input</span>
            <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "#10b981", marginRight: 3 }} />Output</span>
          </div>
        </div>
      )}

      {/* Distribution: by endpoint + by operation type */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
        {/* By endpoint */}
        <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 14 }}>
          <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
            {t("tokenStats.byEndpoint", "按端点")}
          </h3>
          {byEndpoint.length === 0 ? (
            <div style={{ fontSize: 12, opacity: 0.4 }}>{t("tokenStats.noData", "暂无数据")}</div>
          ) : byEndpoint.map((row) => {
            const maxRow = byEndpoint[0]?.total_tokens || 1;
            return (
              <div key={row.group_key} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 2 }}>
                  <span style={{ fontWeight: 600 }}>{row.group_key || "(unknown)"}</span>
                  <span style={{ color: "var(--text-secondary)" }}>{fmtNum(row.total_tokens)}</span>
                </div>
                <MiniBar value={row.total_tokens} max={maxRow} />
              </div>
            );
          })}
        </div>

        {/* By operation type */}
        <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 14 }}>
          <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
            {t("tokenStats.byOperation", "按操作类型")}
          </h3>
          {byOp.length === 0 ? (
            <div style={{ fontSize: 12, opacity: 0.4 }}>{t("tokenStats.noData", "暂无数据")}</div>
          ) : byOp.map((row) => {
            const maxRow = byOp[0]?.total_tokens || 1;
            return (
              <div key={row.group_key} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 2 }}>
                  <span style={{ fontWeight: 600 }}>{row.group_key || "(unknown)"}</span>
                  <span style={{ color: "var(--text-secondary)" }}>{fmtNum(row.total_tokens)} · {row.request_count} reqs</span>
                </div>
                <MiniBar value={row.total_tokens} max={maxRow} color="#8b5cf6" />
              </div>
            );
          })}
        </div>
      </div>

      {/* Sessions table */}
      {sessions.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
            {t("tokenStats.sessions", "按会话")}
          </h3>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)" }}>
                  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Session</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 600 }}>Input</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 600 }}>Output</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 600 }}>Total</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 600 }}>Reqs</th>
                  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Endpoints</th>
                  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Last</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "5px 8px", fontFamily: "monospace", fontSize: 10, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis" }}>{s.session_id}</td>
                    <td style={{ padding: "5px 8px", textAlign: "right" }}>{fmtNum(s.total_input)}</td>
                    <td style={{ padding: "5px 8px", textAlign: "right" }}>{fmtNum(s.total_output)}</td>
                    <td style={{ padding: "5px 8px", textAlign: "right", fontWeight: 600 }}>{fmtNum(s.total_tokens)}</td>
                    <td style={{ padding: "5px 8px", textAlign: "right" }}>{s.request_count}</td>
                    <td style={{ padding: "5px 8px", fontSize: 10 }}>{s.endpoints}</td>
                    <td style={{ padding: "5px 8px", fontSize: 10, color: "var(--text-secondary)" }}>{s.last_call?.replace("T", " ").slice(0, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
