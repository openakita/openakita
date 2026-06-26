// ─── SkillUsageView: 技能用量监控面板 ───
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { IconStatus } from "../icons";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "../components/ui/table";

type DaysKey = 7 | 30 | 90 | 365;

type SkillUsageSummary = {
  total_skill_loads: number;
  total_skill_edits: number;
  total_skill_actions: number;
  distinct_skills_used: number;
};

type DaySkillRow = {
  skill: string;
  load_count: number;
  edit_count: number;
  total_count: number;
};

type DayRow = {
  date: string;
  load_count: number;
  edit_count: number;
  total_count: number;
  skills: DaySkillRow[];
};

type TopSkillRow = {
  skill: string;
  load_count: number;
  edit_count: number;
  total_count: number;
  percentage: number;
  last_used_at: number | null;
};

type SkillUsageStats = {
  period_days: number;
  summary: SkillUsageSummary;
  by_day: DayRow[];
  top_skills: TopSkillRow[];
};

const DAYS_KEYS: DaysKey[] = [7, 30, 90, 365];

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtPct(value: number): string {
  if (!value || value <= 0) return "0%";
  return value >= 10 ? `${value.toFixed(0)}%` : `${value.toFixed(1)}%`;
}

function unixToLocal(ts: number | null): string {
  if (!ts || ts <= 0) return "-";
  const d = new Date(ts * 1000);
  if (isNaN(d.getTime())) return "-";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function shortDate(date: string): string {
  // "YYYY-MM-DD" -> "MM-DD"
  return date.length >= 10 ? date.slice(5) : date;
}

const LOAD_COLOR = "#3b82f6";
const EDIT_COLOR = "#f59e0b";

function MiniBar({ value, max, color = "hsl(var(--primary))" }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min(value / max, 1) * 100 : 0;
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted/60">
      <div
        className="h-full rounded-full transition-[width] duration-300"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

const STAT_COLORS = ["hsl(var(--primary))", "#3b82f6", "#f59e0b", "#10b981"];

export function SkillUsageView({
  serviceRunning,
  apiBaseUrl = "http://127.0.0.1:18900",
}: {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}) {
  const { t } = useTranslation();
  const [days, setDays] = useState<DaysKey>(7);
  const [stats, setStats] = useState<SkillUsageStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setFetchError(false);
    try {
      const res = await safeFetch(
        `${apiBaseUrl}/api/stats/skills/usage/stats?days=${days}`,
        { signal: AbortSignal.timeout(8000) },
      );
      const data = (await res.json()) as SkillUsageStats & { error?: string };
      if (data.error) {
        setFetchError(true);
        setStats(null);
      } else {
        setStats(data);
      }
    } catch {
      setFetchError(true);
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, days]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  useEffect(() => {
    if (serviceRunning && fetchError) fetchAll();
  }, [serviceRunning, fetchError, fetchAll]);

  const byDay = stats?.by_day || [];
  const topSkills = stats?.top_skills || [];
  const summary = stats?.summary;
  const maxDay = Math.max(...byDay.map((r) => r.total_count), 1);

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconStatus size={48} />
        <div className="mt-3 font-semibold">{t("skillUsage.title", "技能用量")}</div>
        <div className="mt-1 text-xs opacity-50">{t("skillUsage.serviceNotRunning", "服务未运行，无法查看统计")}</div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1080px] space-y-6 px-6 py-5">
      {/* ── Header: title + subtitle ── */}
      <div className="flex items-start justify-between gap-4 overflow-x-auto">
        <div className="space-y-1.5 min-w-0">
          <h2 className="truncate text-lg font-bold tracking-tight" title={t("skillUsage.title", "技能用量")}>
            {t("skillUsage.title", "技能用量")}
          </h2>
          <p className="truncate text-xs text-muted-foreground leading-relaxed" title={t("skillUsage.subtitle", "跟踪会话中的技能加载和编辑")}>
            {t("skillUsage.subtitle", "跟踪会话中的技能加载和编辑")}
          </p>
        </div>
      </div>

      {/* ── Period selector ── */}
      <div className="flex items-center gap-1.5 overflow-x-auto whitespace-nowrap">
        {DAYS_KEYS.map((dk) => (
          <Button
            key={dk}
            size="xs"
            variant={days === dk ? "default" : "outline"}
            className="shrink-0"
            onClick={() => setDays(dk)}
          >
            {t("skillUsage.periodLabel", { days: dk, defaultValue: "{{days}}天" })}
          </Button>
        ))}
        <Button size="xs" variant="outline" className="shrink-0" onClick={fetchAll} disabled={loading} title={t("skillUsage.refresh", "刷新")}>
          {loading ? "..." : t("skillUsage.refresh", "刷新")}
        </Button>
      </div>

      {/* ── Summary cards ── */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { label: t("skillUsage.totalActions", "操作"), value: fmtNum(summary.total_skill_actions), color: STAT_COLORS[0] },
            { label: t("skillUsage.loads", "加载"), value: fmtNum(summary.total_skill_loads), color: STAT_COLORS[1] },
            { label: t("skillUsage.edits", "编辑"), value: fmtNum(summary.total_skill_edits), color: STAT_COLORS[2] },
            { label: t("skillUsage.distinctSkills", "技能数"), value: fmtNum(summary.distinct_skills_used), color: STAT_COLORS[3] },
          ].map((card) => (
            <Card key={card.label} className="p-0 gap-0 overflow-hidden border-border/50 shadow-sm">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2 pt-4 px-5">
                <CardTitle className="text-xs font-medium text-muted-foreground">{card.label}</CardTitle>
              </CardHeader>
              <CardContent className="px-5 pb-4 pt-0">
                <div className="text-2xl font-bold tracking-tight" style={{ color: card.color }}>{card.value}</div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* ── Daily trend chart (stacked load + edit) ── */}
      <Card className="p-0 gap-0 border-border/50 shadow-sm">
        <div className="px-5 py-3 border-b border-border/50 flex items-center justify-between">
          <div className="text-sm font-semibold">{t("skillUsage.dailyTrend", "趋势")}</div>
          <div className="text-xs text-muted-foreground">{t("skillUsage.periodSummary", { days, defaultValue: "最近 {{days}} 天" })}</div>
        </div>
        <CardContent className="px-5 pt-4 pb-5 space-y-3">
          {byDay.length === 0 || maxDay <= 0 ? (
            <p className="text-sm text-muted-foreground/50 py-8 text-center">{t("skillUsage.noData", "暂无技能用量数据")}</p>
          ) : (
            <>
              <div className="flex items-end gap-[2px] h-32 rounded-lg bg-muted/20 p-2 border border-border/50">
                {byDay.map((r, i) => {
                  const loadH = (r.load_count / maxDay) * 100;
                  const editH = (r.edit_count / maxDay) * 100;
                  return (
                    <div
                      key={i}
                      className="flex-1 flex flex-col justify-end items-center h-full group relative min-w-[1px]"
                      title={`${r.date}\n${t("skillUsage.loads", "加载")}: ${r.load_count}\n${t("skillUsage.edits", "编辑")}: ${r.edit_count}`}
                    >
                      <div className="w-full h-full flex flex-col justify-end opacity-80 group-hover:opacity-100 transition-opacity">
                        {r.total_count > 0 && (
                          <>
                            <div className="rounded-t-sm w-full" style={{ height: `${editH}%`, background: EDIT_COLOR }} />
                            <div className="rounded-b-sm w-full min-h-[2px]" style={{ height: `${loadH}%`, background: LOAD_COLOR }} />
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="flex justify-between text-[11px] text-muted-foreground px-1">
                <span>{shortDate(byDay[0]?.date || "")}</span>
                <span>{shortDate(byDay[byDay.length - 1]?.date || "")}</span>
              </div>
              <div className="flex gap-4 text-xs text-muted-foreground pt-1">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-2.5 h-2.5 rounded-[2px]" style={{ background: LOAD_COLOR }} />{t("skillUsage.loads", "加载")}
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-2.5 h-2.5 rounded-[2px]" style={{ background: EDIT_COLOR }} />{t("skillUsage.edits", "编辑")}
                </span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* ── Top skills table ── */}
      <Card className="p-0 gap-0 border-border/50 shadow-sm overflow-hidden">
        <div className="px-5 py-3 border-b border-border/50 flex items-center justify-between">
          <div className="text-sm font-semibold">{t("skillUsage.topSkills", "热门")}</div>
          <div className="text-xs text-muted-foreground">{t("skillUsage.periodSummary", { days, defaultValue: "最近 {{days}} 天" })}</div>
        </div>
        <CardContent className="p-0">
          {topSkills.length === 0 ? (
            <p className="text-sm text-muted-foreground/50 py-8 text-center">{t("skillUsage.noData", "暂无技能用量数据")}</p>
          ) : (
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow className="hover:bg-transparent">
                  <TableHead className="text-xs h-10 px-5 font-medium">{t("skillUsage.skill", "技能")}</TableHead>
                  <TableHead className="text-xs h-10 px-4 text-right font-medium">{t("skillUsage.loads", "加载")}</TableHead>
                  <TableHead className="text-xs h-10 px-4 text-right font-medium">{t("skillUsage.edits", "编辑")}</TableHead>
                  <TableHead className="text-xs h-10 px-4 font-medium w-[180px]">{t("skillUsage.share", "占比")}</TableHead>
                  <TableHead className="text-xs h-10 px-5 font-medium">{t("skillUsage.lastUsed", "最近")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {topSkills.map((s) => (
                  <TableRow key={s.skill} className="border-b-border/50 transition-colors hover:bg-muted/20">
                    <TableCell className="px-5 py-3 text-xs font-medium max-w-[220px] truncate" title={s.skill}>{s.skill}</TableCell>
                    <TableCell className="px-4 py-3 text-xs text-right font-mono text-muted-foreground">{s.load_count}</TableCell>
                    <TableCell className="px-4 py-3 text-xs text-right font-mono text-muted-foreground">{s.edit_count}</TableCell>
                    <TableCell className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <MiniBar value={s.percentage} max={100} color={LOAD_COLOR} />
                        <span className="text-[11px] text-muted-foreground font-mono shrink-0 w-12 text-right">{fmtPct(s.percentage)}</span>
                      </div>
                    </TableCell>
                    <TableCell className="px-5 py-3 text-xs text-muted-foreground whitespace-nowrap">{unixToLocal(s.last_used_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default SkillUsageView;
