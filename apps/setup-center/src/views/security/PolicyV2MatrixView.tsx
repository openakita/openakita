// C23 P2-1: Policy V2 自动批准矩阵 UI
//
// Plan §13 / R5-12 / C9 要求 SecurityView 暴露给用户的两层结构:
//   1. session_role (4): plan / ask / agent / coordinator
//      —— PLAN / ASK 模式禁止任何 write intent, 不管 confirmation_mode 是什么
//   2. confirmation_mode (5) × ApprovalClass (11):
//      —— 给出 "在 X 模式下, Y 类操作自动 (ALLOW / CONFIRM / DENY)"
//
// 这个组件**不是 live editor** —— policy_v2 的矩阵在 engine.py 12-step
// 决策链里, 不存在单一可序列化的"行 = (class, mode), 值 = decision"映射,
// 因为还要叠加 safety_immune / unattended / mode_ruleset 等条件. 所以
// 这里渲染的是**文档化静态矩阵**, 来源是 engine.py 注释 + plan §3.
//
// 与后端一致性守卫: tests/unit/test_c23_policy_v2_matrix.py 会比较本
// 文件的 MATRIX 常量与 engine.py 的关键决策分支, 漂移就 fail.

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

type Decision = "allow" | "confirm" | "deny";

type MatrixRow = {
  role: string;
  approval_class: string;
  decisions: Record<string, Decision>;
};

type MatrixResponse = {
  roles: string[];
  modes: string[];
  classes: string[];
  rows: MatrixRow[];
  baseline_only: boolean;
};

const DECISION_META: Record<Decision, { label: string; color: string; bg: string }> = {
  allow:   { label: "ALLOW",   color: "#16a34a", bg: "#22c55e1a" },
  confirm: { label: "CONFIRM", color: "#d97706", bg: "#f59e0b1a" },
  deny:    { label: "DENY",    color: "#dc2626", bg: "#ef44441a" },
};

function DecisionCell({ decision }: { decision: Decision }) {
  const meta = DECISION_META[decision];
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.04em",
        color: meta.color,
        background: meta.bg,
        border: `1px solid ${meta.color}44`,
      }}
    >
      {meta.label}
    </span>
  );
}

export function PolicyV2MatrixView({ apiBaseUrl }: { apiBaseUrl: string }) {
  const { t } = useTranslation();
  const [data, setData] = useState<MatrixResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl}/api/config/security/approval-matrix`)
      .then((res) => res.json())
      .then((json) => {
        if (!cancelled) setData(json);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl]);

  const groupedRows = useMemo(() => {
    const rows = data?.rows || [];
    return (data?.roles || []).map((role) => ({
      role,
      rows: rows.filter((row) => row.role === role),
    }));
  }, [data]);

  return (
    <div className="space-y-4">
      {/* Session role panel */}
      <Card className="p-0 gap-0 border-border/50 shadow-sm">
        <CardHeader className="border-b border-border/50 px-4 py-2.5">
          <CardTitle className="text-sm font-semibold">
            {t("security.matrixSessionRoleTitle", "Session Role（会话角色）")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 px-4 py-3 text-xs">
          <p className="text-muted-foreground leading-5">
            {t(
              "security.matrixSessionRoleDesc",
              "Session role 与 confirmation_mode 正交。Plan / Ask 模式会在引擎 step 4 直接拦截任何写意图（不论下方矩阵给什么），用于"+
                "纯只读探查会话；Agent / Coordinator 走下方矩阵。",
            )}
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {(data?.roles || []).map((role) => (
              <div
                key={role}
                className="rounded-md border border-border/50 bg-muted/30 px-3 py-2"
              >
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant="outline" className="text-[10px] uppercase">{role}</Badge>
                  <span className="text-sm font-medium">{role}</span>
                </div>
                <div className="text-xs text-muted-foreground">由后端 lookup_matrix 生成</div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* ApprovalClass × ConfirmationMode 矩阵 */}
      <Card className="p-0 gap-0 border-border/50 shadow-sm">
        <CardHeader className="border-b border-border/50 px-4 py-2.5">
          <CardTitle className="text-sm font-semibold">
            {t("security.matrixTitle", "审批矩阵：ApprovalClass × ConfirmationMode")}
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 py-3 text-xs">
          <p className="text-muted-foreground leading-5 mb-3">
            {t(
              "security.matrixDesc",
              "下表展示当前 policy_v2 引擎在 Agent / Coordinator 角色下、对每个 ApprovalClass 的默认行为。具体决策"+
                "还会受 safety_immune（永不放行的 forbidden zone）、unattended（owner 是否在线）、custom override（POLICIES.yaml"+
                "approval_classes.overrides）影响；下表给出的是没有任何特殊命中时的 baseline。",
            )}
          </p>
          {error && <p className="text-xs text-destructive">{error}</p>}
          {!data && !error && <p className="text-xs text-muted-foreground">正在读取后端审批矩阵...</p>}
          {data && <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-border/50">
                  <th className="text-left py-2 pr-3 font-medium text-muted-foreground">
                    {t("security.matrixColClass", "ApprovalClass")}
                  </th>
                  {data.modes.map((mode) => (
                    <th key={mode} className="text-center py-2 px-2 font-medium">
                      <div className="text-sm">{mode}</div>
                      <div className="text-[10px] text-muted-foreground font-normal mt-0.5">confirmation_mode</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {groupedRows.flatMap(({ role, rows }) => [
                  <tr key={`${role}-header`} className="bg-muted/30">
                    <td colSpan={data.modes.length + 1} className="py-2 font-semibold uppercase">
                      {role}
                    </td>
                  </tr>,
                  ...rows.map((row) => (
                    <tr key={`${row.role}-${row.approval_class}`} className="border-b border-border/30 last:border-b-0">
                      <td className="py-2 pr-3">
                        <code className="text-[10px] text-muted-foreground">{row.approval_class}</code>
                      </td>
                      {data.modes.map((mode) => (
                        <td key={mode} className="text-center py-2 px-2">
                          <DecisionCell decision={row.decisions[mode]} />
                        </td>
                      ))}
                    </tr>
                  )),
                ])}
              </tbody>
            </table>
          </div>}

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-3 mt-4 pt-3 border-t border-border/50 text-[11px]">
            <span className="text-muted-foreground">{t("security.matrixLegend", "图例：")}</span>
            <span className="inline-flex items-center gap-1.5">
              <DecisionCell decision="allow" />
              <span>{t("security.matrixLegendAllow", "自动放行")}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <DecisionCell decision="confirm" />
              <span>{t("security.matrixLegendConfirm", "弹窗确认")}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <DecisionCell decision="deny" />
              <span>{t("security.matrixLegendDeny", "拒绝执行")}</span>
            </span>
          </div>

          <p className="text-[10px] text-muted-foreground mt-3 italic">
            {t(
              "security.matrixDataSource",
              "数据源：后端 lookup_matrix()。这里只展示 baseline，不包含路径名单、敏感路径保护、owner_only、death_switch、trusted_path 等后续叠加。",
            )}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// Matrix data now comes from /api/config/security/approval-matrix.
