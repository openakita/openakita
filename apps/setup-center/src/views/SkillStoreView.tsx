import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { safeFetch } from "../providers";

interface Skill {
  id: string;
  name: string;
  description: string;
  category: string;
  trustLevel: "official" | "certified" | "community";
  authorName?: string;
  installCount: number;
  avgRating?: number;
  ratingCount?: number;
  version?: string;
  githubStars?: number;
  sourceRepo?: string;
  license?: string;
}

interface SkillStoreViewProps {
  apiBaseUrl: string;
  visible: boolean;
}

const skillUniqueKey = (s: Skill): string =>
  s.sourceRepo ? `${s.sourceRepo}::${s.id}` : s.id;

export function SkillStoreView({ apiBaseUrl, visible }: SkillStoreViewProps) {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [trustLevel, setTrustLevel] = useState("");
  const [sort, setSort] = useState("installs");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [installingSet, setInstallingSet] = useState<Set<string>>(new Set());
  const [confirmSkill, setConfirmSkill] = useState<Skill | null>(null);

  const trustBadge = (level: string) => {
    switch (level) {
      case "official":
        return { label: t("skillStore.trustOfficial"), color: "var(--accent, #1d4ed8)", bg: "rgba(59,130,246,0.12)" };
      case "certified":
        return { label: t("skillStore.trustCertified"), color: "var(--success, #15803d)", bg: "rgba(34,197,94,0.12)" };
      default:
        return { label: t("skillStore.trustCommunity"), color: "var(--muted)", bg: "rgba(107,114,128,0.12)" };
    }
  };

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ sort, page: String(page), limit: "20" });
      if (query) params.set("q", query);
      if (category) params.set("category", category);
      if (trustLevel) params.set("trust_level", trustLevel);
      const resp = await safeFetch(`${apiBaseUrl}/api/hub/skills?${params}`);
      const data = await resp.json();
      setSkills(data.skills || data.data || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message || t("skillStore.connectFail"));
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, query, category, trustLevel, sort, page, t]);

  useEffect(() => {
    if (visible) fetchSkills();
  }, [visible, fetchSkills]);

  const doInstall = async (skill: Skill) => {
    const key = skillUniqueKey(skill);
    setInstallingSet(prev => { const next = new Set(prev); next.add(key); return next; });
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/hub/skills/${skill.id}/install`, { method: "POST" });
      const data = await resp.json();
      toast.success(t("skillStore.installSuccess", { name: data.skill_name || skill.name }));
      safeFetch(`${apiBaseUrl}/api/skills/reload`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
    } catch (e: any) {
      toast.error(t("skillStore.installFail", { msg: e.message }));
    } finally {
      setInstallingSet(prev => { const next = new Set(prev); next.delete(key); return next; });
    }
  };

  if (!visible) return null;

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="cardTitle">{t("skillStore.title", "Skill Store")}</h2>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "4px 0 12px" }}>
          {t("skillStore.subtitle")}
        </p>

        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <input
            type="text"
            placeholder={t("skillStore.searchPlaceholder")}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            onKeyDown={(e) => e.key === "Enter" && fetchSkills()}
            style={{ flex: 1, width: "auto", minWidth: 120, maxWidth: 220 }}
          />
          <select value={trustLevel} onChange={(e) => { setTrustLevel(e.target.value); setPage(1); }} style={{ width: "auto", minWidth: 0 }}>
            <option value="">{t("skillStore.allTrust")}</option>
            <option value="official">{t("skillStore.trustOfficial")}</option>
            <option value="certified">{t("skillStore.trustCertified")}</option>
            <option value="community">{t("skillStore.trustCommunity")}</option>
          </select>
          <select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }} style={{ width: "auto", minWidth: 0 }}>
            <option value="">{t("skillStore.allCategories")}</option>
            <option value="general">{t("skillStore.catGeneral")}</option>
            <option value="development">{t("skillStore.catDevelopment")}</option>
            <option value="productivity">{t("skillStore.catProductivity")}</option>
            <option value="data">{t("skillStore.catData")}</option>
            <option value="creative">{t("skillStore.catCreative")}</option>
            <option value="communication">{t("skillStore.catCommunication")}</option>
          </select>
          <select value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }} style={{ width: "auto", minWidth: 0 }}>
            <option value="installs">{t("skillStore.sortInstalls")}</option>
            <option value="rating">{t("skillStore.sortRating")}</option>
            <option value="newest">{t("skillStore.sortNewest")}</option>
            <option value="stars">{t("skillStore.sortStars")}</option>
          </select>
          <button onClick={fetchSkills} disabled={loading} style={{ whiteSpace: "nowrap" }}>
            {loading ? t("skillStore.searching") : t("common.search")}
          </button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ textAlign: "center", padding: "24px 16px" }}>
          <p style={{ color: "var(--error, #dc2626)", marginBottom: 8 }}>{t("skillStore.connectFail")}</p>
          <p style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6 }}>
            {t("skillStore.offlineHint")}
          </p>
          <button onClick={fetchSkills} style={{ marginTop: 12 }}>{t("skillStore.retry")}</button>
        </div>
      )}

      {!loading && !error && skills.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: 40 }}>
          <p style={{ color: "var(--muted)", fontSize: 15 }}>{t("skillStore.empty")}</p>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
        {skills.map((s) => {
          const badge = trustBadge(s.trustLevel);
          const uk = skillUniqueKey(s);
          return (
            <div key={uk} className="card" style={{ position: "relative" }}>
              <span style={{
                position: "absolute", top: 8, right: 8, fontSize: 10, padding: "2px 6px",
                background: badge.bg, color: badge.color, borderRadius: 4, fontWeight: 600,
              }}>
                {badge.label}
              </span>
              <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4, fontFamily: "monospace" }}>
                {s.name}
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, lineHeight: 1.5 }}>
                {s.description?.slice(0, 120) || t("skillStore.noDesc")}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12, color: "var(--muted)", marginBottom: 8, flexWrap: "wrap" }}>
                <span>{t("skillStore.installs", { count: s.installCount })}</span>
                {s.avgRating != null && s.avgRating > 0 && <span>{s.avgRating.toFixed(1)}</span>}
                {s.githubStars != null && s.githubStars > 0 && <span>{s.githubStars} stars</span>}
                {s.version && <span>v{s.version}</span>}
                {s.authorName && <span>by {s.authorName}</span>}
                {s.license && (
                  <span style={{
                    fontSize: 10, padding: "1px 5px", borderRadius: 3,
                    background: "rgba(139,92,246,0.12)", color: "var(--accent, #7c3aed)", fontWeight: 500,
                  }}>
                    {s.license}
                  </span>
                )}
              </div>
              {s.sourceRepo && (
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
                  <a
                    href={`https://github.com/${s.sourceRepo}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "var(--accent, #5B8DEF)", textDecoration: "none" }}
                  >
                    {s.sourceRepo}
                  </a>
                </div>
              )}
              <button
                onClick={() => setConfirmSkill(s)}
                disabled={installingSet.has(uk)}
                style={{ width: "100%", marginTop: 4 }}
              >
                {installingSet.has(uk) ? t("skillStore.installing") : t("skillStore.install")}
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

      {confirmSkill && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 9999,
            background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center",
          }}
          onClick={() => setConfirmSkill(null)}
        >
          <div
            className="card"
            style={{ maxWidth: 420, width: "90%", padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>{t("skillStore.confirmTitle")}</h3>
            <p style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6, margin: "0 0 8px" }}>
              {t("skillStore.confirmDesc", { name: confirmSkill.name })}
            </p>
            {confirmSkill.license && (
              <p style={{ fontSize: 12, margin: "0 0 4px" }}>
                <span style={{ fontWeight: 500 }}>{t("skillStore.license")}:</span>{" "}
                <span style={{ padding: "1px 5px", borderRadius: 3, background: "rgba(139,92,246,0.12)", color: "var(--accent, #7c3aed)" }}>
                  {confirmSkill.license}
                </span>
              </p>
            )}
            {confirmSkill.sourceRepo && (
              <p style={{ fontSize: 12, margin: "0 0 4px" }}>
                <span style={{ fontWeight: 500 }}>{t("skillStore.source")}:</span>{" "}
                <a href={`https://github.com/${confirmSkill.sourceRepo}`} target="_blank" rel="noopener noreferrer"
                  style={{ color: "var(--accent, #5B8DEF)" }}>
                  {confirmSkill.sourceRepo}
                </a>
              </p>
            )}
            <p style={{ fontSize: 11, color: "var(--muted)", margin: "8px 0 16px", lineHeight: 1.5 }}>
              {t("skillStore.licenseNotice")}
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={() => setConfirmSkill(null)}>{t("common.cancel")}</button>
              <button
                className="btnPrimary"
                onClick={() => { const s = confirmSkill!; setConfirmSkill(null); doInstall(s); }}
              >
                {t("skillStore.confirmInstall")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
