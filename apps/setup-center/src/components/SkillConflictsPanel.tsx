import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from "lucide-react";
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
  if (src.origin) parts.push(describeOrigin(src.origin));
  if (src.plugin_source) parts.push(src.plugin_source);
  if (src.path) parts.push(src.path);
  return parts.join(" · ") || "—";
}

function describeOrigin(origin?: string): string {
  switch (origin) {
    case "remote":
      return "远程安装";
    case "project":
      return "本地项目";
    case "system":
      return "系统内置";
    case "marketplace":
      return "技能市场";
    case "plugin":
      return "插件提供";
    default:
      return origin || "未知来源";
  }
}

export function SkillConflictsPanel({ httpApiBase }: SkillConflictsPanelProps) {
  const { t } = useTranslation();
  const [conflicts, setConflicts] = useState<SkillConflict[]>([]);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [statusText, setStatusText] = useState("");

  const refresh = async (showResult = true) => {
    setLoading(true);
    try {
      const resp = await safeFetch(`${httpApiBase()}/api/skills/conflicts`);
      if (resp.ok) {
        const body = (await resp.json()) as { conflicts?: SkillConflict[] };
        const next = Array.isArray(body.conflicts) ? body.conflicts : [];
        setConflicts(next);
        if (showResult) {
          setStatusText(next.length > 0 ? "已刷新，仍有同名来源需要确认。" : "已刷新，当前没有同名来源提示。");
        }
      } else if (showResult) {
        setStatusText("刷新失败，请稍后再试。");
      }
    } catch {
      if (showResult) setStatusText("刷新失败，请检查后端服务是否在线。");
    } finally {
      setLoading(false);
    }
  };

  const clearConflicts = async () => {
    setClearing(true);
    try {
      const resp = await safeFetch(`${httpApiBase()}/api/skills/conflicts/clear`, {
        method: "POST",
      });
      if (resp.ok) {
        setConflicts([]);
        setExpanded(false);
        setStatusText("已清除提示。技能文件不会被删除，当前生效技能保持不变。");
      } else {
        setStatusText("清除失败，请稍后再试。");
      }
    } catch {
      setStatusText("清除失败，请检查后端服务是否在线。");
    } finally {
      setClearing(false);
    }
  };

  useEffect(() => {
    refresh(false);
    const onChange = () => {
      // Slight defer so the backend has a tick to update the registry.
      setTimeout(() => refresh(false), 200);
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
          {t("status.skillConflicts.title", { defaultValue: "同名技能来源提示" })}
        </div>
        <div className="statusPanelDesc">
          {total === 0 ? (
            <span style={{ opacity: 0.7 }}>
              {t("status.skillConflicts.empty", {
                defaultValue: "未发现同名技能来源",
              })}
            </span>
          ) : (
            <span style={{ color: "#c0392b" }}>
              {t("status.skillConflicts.nonEmpty", {
                defaultValue: "发现 {{count}} 个技能名来自多个来源，OpenAkita 已自动只保留一个生效。",
                count: total,
              })}
            </span>
          )}
          {statusText && (
            <div style={{ marginTop: 4, fontSize: 12, opacity: 0.75 }}>
              {statusText}
            </div>
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
                const action = c.action === "overridden" ? "已自动使用较新的来源" : "已保留原来源";
                return (
                  <li key={i} style={{ marginBottom: 4 }}>
                    <strong>{c.skill_id || c.name || "(unknown)"}</strong> · {action}
                    <div style={{ opacity: 0.75 }}>
                      当前生效：{describeSource(c.winner)}
                    </div>
                    <div style={{ opacity: 0.6 }}>
                      已忽略的同名来源：{describeSource(c.shadowed)}
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
        {total > 0 && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs px-2.5"
            onClick={clearConflicts}
            disabled={clearing || loading}
            title="只清除这条提示记录，不删除技能。"
          >
            {clearing ? <RefreshCw size={12} className="animate-spin" /> : <XCircle size={12} />}
            清除提示
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs px-2.5"
          onClick={() => refresh(true)}
          disabled={loading || clearing}
        >
          {loading ? <RefreshCw size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
          {loading ? "刷新中" : t("status.skillConflicts.refresh", { defaultValue: "刷新状态" })}
        </Button>
      </div>
    </div>
  );
}
