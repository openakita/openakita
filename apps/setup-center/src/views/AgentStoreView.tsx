import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { safeFetch } from "../providers";

interface Agent {
  id: string;
  name: string;
  description: string;
  category: string;
  authorName?: string;
  downloads: number;
  avgRating?: number;
  ratingCount?: number;
  latestVersion?: string;
  tags?: string[];
  isFeatured?: boolean;
  license?: string;
}

interface AgentStoreViewProps {
  apiBaseUrl: string;
  visible: boolean;
}

const agentUniqueKey = (a: Agent): string =>
  a.authorName ? `${a.authorName}::${a.id}` : a.id;

export function AgentStoreView({ apiBaseUrl, visible }: AgentStoreViewProps) {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("downloads");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [installingSet, setInstallingSet] = useState<Set<string>>(new Set());
  const [confirmAgent, setConfirmAgent] = useState<Agent | null>(null);

  const fetchAgents = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ sort, page: String(page), limit: "20" });
      if (query) params.set("q", query);
      if (category) params.set("category", category);
      const resp = await safeFetch(`${apiBaseUrl}/api/hub/agents?${params}`);
      const data = await resp.json();
      setAgents(data.agents || data.data || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message || t("agentStore.connectFail"));
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, query, category, sort, page, t]);

  useEffect(() => {
    if (visible) fetchAgents();
  }, [visible, fetchAgents]);

  const doInstall = async (agent: Agent) => {
    const key = agentUniqueKey(agent);
    setInstallingSet(prev => { const next = new Set(prev); next.add(key); return next; });
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/hub/agents/${agent.id}/install`, { method: "POST" });
      const data = await resp.json();
      toast.success(t("agentStore.installSuccess", { name: data.profile?.name || agent.name }));
      safeFetch(`${apiBaseUrl}/api/skills/reload`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
    } catch (e: any) {
      toast.error(t("agentStore.installFail", { msg: e.message }));
    } finally {
      setInstallingSet(prev => { const next = new Set(prev); next.delete(key); return next; });
    }
  };

  if (!visible) return null;

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="cardTitle">Agent Store</h2>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "4px 0 12px" }}>
          {t("agentStore.subtitle")}
        </p>

        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <input
            type="text"
            placeholder={t("agentStore.searchPlaceholder")}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            onKeyDown={(e) => e.key === "Enter" && fetchAgents()}
            style={{ flex: 1, width: "auto", minWidth: 120, maxWidth: 260 }}
          />
          <select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }} style={{ width: "auto", minWidth: 0 }}>
            <option value="">{t("agentStore.allCategories")}</option>
            <option value="customer_service">{t("agentStore.catCustomerService")}</option>
            <option value="development">{t("agentStore.catDevelopment")}</option>
            <option value="business">{t("agentStore.catBusiness")}</option>
            <option value="creative">{t("agentStore.catCreative")}</option>
            <option value="education">{t("agentStore.catEducation")}</option>
            <option value="productivity">{t("agentStore.catProductivity")}</option>
            <option value="general">{t("agentStore.catGeneral")}</option>
          </select>
          <select value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }} style={{ width: "auto", minWidth: 0 }}>
            <option value="downloads">{t("agentStore.sortDownloads")}</option>
            <option value="rating">{t("agentStore.sortRating")}</option>
            <option value="newest">{t("agentStore.sortNewest")}</option>
          </select>
          <button onClick={fetchAgents} disabled={loading} style={{ whiteSpace: "nowrap" }}>
            {loading ? t("agentStore.searching") : t("common.search")}
          </button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ textAlign: "center", padding: "24px 16px" }}>
          <p style={{ color: "var(--error, #dc2626)", marginBottom: 8 }}>{error || t("agentStore.connectFail")}</p>
          <p style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6 }}>
            {t("agentStore.offlineHint")}
          </p>
          <button onClick={fetchAgents} style={{ marginTop: 12 }}>{t("agentStore.retry")}</button>
        </div>
      )}

      {!loading && !error && agents.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: 28 }}>
          <p style={{ color: "var(--muted)", fontSize: 13 }}>{t("agentStore.empty")}</p>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
        {agents.map((a) => {
          const uk = agentUniqueKey(a);
          return (
            <div key={uk} className="card" style={{ position: "relative" }}>
              {a.isFeatured && (
                <span style={{
                  position: "absolute", top: 8, right: 8, fontSize: 10, padding: "2px 6px",
                  background: "var(--accent)", color: "#fff", borderRadius: 4, fontWeight: 600,
                }}>
                  {t("agentStore.featured")}
                </span>
              )}
              <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>
                {a.name}
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, lineHeight: 1.5 }}>
                {a.description?.slice(0, 120) || t("agentStore.noDesc")}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12, color: "var(--muted)", marginBottom: 8, flexWrap: "wrap" }}>
                <span>{t("agentStore.downloads", { count: a.downloads })}</span>
                {a.avgRating != null && a.avgRating > 0 && <span>{a.avgRating.toFixed(1)}</span>}
                {a.latestVersion && <span>v{a.latestVersion}</span>}
                {a.authorName && <span>by {a.authorName}</span>}
                {a.license && (
                  <span style={{
                    fontSize: 10, padding: "1px 5px", borderRadius: 3,
                    background: "rgba(139,92,246,0.12)", color: "var(--accent, #7c3aed)", fontWeight: 500,
                  }}>
                    {a.license}
                  </span>
                )}
              </div>
              {a.tags && a.tags.length > 0 && (
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 8 }}>
                  {a.tags.slice(0, 4).map((tag) => (
                    <span key={tag} style={{
                      fontSize: 10, padding: "2px 6px", borderRadius: 4,
                      background: "var(--bg-hover, rgba(0,0,0,0.05))", color: "var(--muted)",
                    }}>
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              <button
                onClick={() => setConfirmAgent(a)}
                disabled={installingSet.has(uk)}
                style={{ width: "100%", marginTop: 4 }}
              >
                {installingSet.has(uk) ? t("agentStore.installing") : t("agentStore.install")}
              </button>
            </div>
          );
        })}
      </div>

      {total > 20 && (
        <div style={{ display: "flex", justifyContent: "center", gap: 8, marginTop: 16 }}>
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>{t("common.prevPage")}</button>
          <span style={{ fontSize: 13, color: "var(--muted)", lineHeight: "32px" }}>
            {t("common.pageInfo", { page, total: Math.ceil(total / 20) })}
          </span>
          <button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>{t("common.nextPage")}</button>
        </div>
      )}

      {confirmAgent && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 9999,
            background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center",
          }}
          onClick={() => setConfirmAgent(null)}
        >
          <div
            className="card"
            style={{ maxWidth: 420, width: "90%", padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>{t("agentStore.confirmTitle")}</h3>
            <p style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6, margin: "0 0 8px" }}>
              {t("agentStore.confirmDesc", { name: confirmAgent.name })}
            </p>
            {confirmAgent.license && (
              <p style={{ fontSize: 12, margin: "0 0 8px" }}>
                <span style={{ fontWeight: 500 }}>{t("agentStore.license")}:</span>{" "}
                <span style={{ padding: "1px 5px", borderRadius: 3, background: "rgba(139,92,246,0.12)", color: "var(--accent, #7c3aed)" }}>
                  {confirmAgent.license}
                </span>
              </p>
            )}
            <p style={{ fontSize: 11, color: "var(--muted)", margin: "0 0 16px", lineHeight: 1.5 }}>
              {t("agentStore.licenseNotice")}
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={() => setConfirmAgent(null)}>{t("common.cancel")}</button>
              <button
                className="btnPrimary"
                onClick={() => { const a = confirmAgent!; setConfirmAgent(null); doInstall(a); }}
              >
                {t("agentStore.confirmInstall")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
