import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { safeFetch } from "../providers";

type ConflictSource = {
  origin?: string;
  plugin_source?: string;
  path?: string;
};

type SkillConflict = {
  skill_id?: string;
  name?: string;
  action?: "rejected" | "overridden" | string;
  winner?: ConflictSource;
  shadowed?: ConflictSource;
};

export interface SkillConflictsPanelProps {
  httpApiBase: () => string;
}

function describeSource(src?: ConflictSource): string {
  if (!src) return "—";
  const parts: string[] = [];
  if (src.origin) parts.push(src.origin);
  if (src.plugin_source) parts.push(src.plugin_source);
  if (src.path) parts.push(src.path);
  return parts.join(" · ") || "—";
}

export function SkillConflictsPanel({ httpApiBase }: SkillConflictsPanelProps) {
  const { t } = useTranslation();
  const [conflicts, setConflicts] = useState<SkillConflict[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const resp = await safeFetch(`${httpApiBase()}/api/skills/conflicts`);
      if (resp.ok) {
        const body = (await resp.json()) as { conflicts?: SkillConflict[] };
        setConflicts(Array.isArray(body.conflicts) ? body.conflicts : []);
      }
    } catch {
      // best-effort
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const onChange = () => {
      // Slight defer so the backend has a tick to update the registry.
      setTimeout(refresh, 200);
    };
    window.addEventListener("openakita:skills-changed", onChange);
    const tabFocus = () => {
      if (!document.hidden) refresh();
    };
    document.addEventListener("visibilitychange", tabFocus);
    return () => {
      window.removeEventListener("openakita:skills-changed", onChange);
      document.removeEventListener("visibilitychange", tabFocus);
    };
  }, []);

  const total = conflicts.length;

  return (
    <div className="statusPanelRow">
      <div className="statusPanelIcon">
        <AlertTriangle size={18} />
      </div>
      <div className="statusPanelInfo" style={{ minWidth: 0 }}>
        <div className="statusPanelTitle">
          {t("status.skillConflicts.title", { defaultValue: "技能加载冲突" })}
        </div>
        <div className="statusPanelDesc">
          {total === 0 ? (
            <span style={{ opacity: 0.7 }}>
              {t("status.skillConflicts.empty", {
                defaultValue: "未检测到技能加载冲突",
              })}
            </span>
          ) : (
            <span style={{ color: "#c0392b" }}>
              {t("status.skillConflicts.nonEmpty", {
                defaultValue: "检测到 {{count}} 处技能同名冲突，点击展开查看",
                count: total,
              })}
            </span>
          )}
          {expanded && total > 0 && (
            <ul
              style={{
                marginTop: 6,
                paddingLeft: 16,
                fontSize: 12,
                opacity: 0.85,
                maxHeight: 180,
                overflow: "auto",
              }}
            >
              {conflicts.map((c, i) => {
                const action = c.action === "overridden" ? "覆盖" : "被拒绝";
                return (
                  <li key={i} style={{ marginBottom: 4 }}>
                    <strong>{c.skill_id || c.name || "(unknown)"}</strong> · {action}
                    <div style={{ opacity: 0.75 }}>
                      生效：{describeSource(c.winner)}
                    </div>
                    <div style={{ opacity: 0.6 }}>
                      被遮蔽：{describeSource(c.shadowed)}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
      <div className="statusPanelActions" style={{ display: "flex", gap: 6 }}>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs px-2.5"
          onClick={() => setExpanded((v) => !v)}
          disabled={total === 0}
        >
          {expanded
            ? t("status.skillConflicts.collapse", { defaultValue: "收起" })
            : t("status.skillConflicts.expand", { defaultValue: "查看详情" })}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs px-2.5"
          onClick={refresh}
          disabled={loading}
        >
          <RefreshCw size={12} />
          {t("status.skillConflicts.refresh", { defaultValue: "刷新" })}
        </Button>
      </div>
    </div>
  );
}
