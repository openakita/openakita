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

import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// === 与 src/openakita/core/policy_v2/enums.py 对齐 ===

const CONFIRMATION_MODES = [
  { id: "trust",        label: "信任 (trust)",          desc: "全部静默放行（除 immune）" },
  { id: "default",      label: "默认 (default)",        desc: "中高风险确认，低风险放行" },
  { id: "accept_edits", label: "接受编辑 (accept_edits)", desc: "写入类自动放行，破坏类仍确认" },
  { id: "strict",       label: "严格 (strict)",         desc: "几乎全确认，破坏类拒绝" },
  { id: "dont_ask",     label: "不打扰 (dont_ask)",     desc: "拒绝所有需确认操作（cron 友好）" },
] as const;

const SESSION_ROLES = [
  { id: "plan",        label: "Plan",        desc: "只读规划 — 写意图全部 DENY" },
  { id: "ask",         label: "Ask",         desc: "信息查询 — 写意图全部 DENY" },
  { id: "agent",       label: "Agent",       desc: "正常执行 — 走下方矩阵" },
  { id: "coordinator", label: "Coordinator", desc: "多 agent 协调 — 走下方矩阵" },
] as const;

type Decision = "allow" | "confirm" | "deny";

// 11 ApprovalClass × 5 ConfirmationMode 矩阵.
// 数据源: src/openakita/core/policy_v2/engine.py 决策链 + plan §3 表.
// 任何修改必须同步 engine.py + tests/unit/test_c23_policy_v2_matrix.py.
const MATRIX: Array<{
  klass: string;
  label: string;
  desc: string;
  trust: Decision;
  default: Decision;
  accept_edits: Decision;
  strict: Decision;
  dont_ask: Decision;
}> = [
  // 只读类 — 任何模式都 ALLOW
  { klass: "readonly_scoped",  label: "局部只读",   desc: "list_directory / read_file 等",  trust: "allow",   default: "allow",   accept_edits: "allow",   strict: "allow",   dont_ask: "allow" },
  { klass: "readonly_global",  label: "全局只读",   desc: "search_codebase 全工作区",        trust: "allow",   default: "allow",   accept_edits: "allow",   strict: "allow",   dont_ask: "allow" },
  { klass: "readonly_search",  label: "搜索",       desc: "grep / glob",                    trust: "allow",   default: "allow",   accept_edits: "allow",   strict: "allow",   dont_ask: "allow" },

  // 修改类 — 严重程度递增
  { klass: "mutating_scoped",  label: "局部副作用", desc: "write_file 单文件",              trust: "allow",   default: "allow",   accept_edits: "allow",   strict: "confirm", dont_ask: "deny"    },
  { klass: "mutating_global",  label: "全局副作用", desc: "bulk_edit / repo 级 patch",      trust: "allow",   default: "confirm", accept_edits: "confirm", strict: "confirm", dont_ask: "deny"    },
  { klass: "destructive",      label: "破坏性",     desc: "delete_file / drop_database",    trust: "confirm", default: "confirm", accept_edits: "confirm", strict: "deny",    dont_ask: "deny"    },

  // 执行类
  { klass: "exec_low_risk",    label: "低危执行",   desc: "run_shell echo / ls",            trust: "allow",   default: "allow",   accept_edits: "allow",   strict: "confirm", dont_ask: "deny"    },
  { klass: "exec_capable",     label: "高权执行",   desc: "run_shell 任意 / opencli_run",   trust: "confirm", default: "confirm", accept_edits: "confirm", strict: "confirm", dont_ask: "deny"    },

  // 控制 / 交互 / 网络
  { klass: "control_plane",    label: "控制面",     desc: "schedule_task / spawn_agent",    trust: "confirm", default: "confirm", accept_edits: "confirm", strict: "confirm", dont_ask: "deny"    },
  { klass: "interactive",      label: "交互式",     desc: "ask_user 问询",                  trust: "allow",   default: "allow",   accept_edits: "allow",   strict: "allow",   dont_ask: "deny"    },
  { klass: "network_out",      label: "网络出站",   desc: "web_search / fetch_url",         trust: "allow",   default: "allow",   accept_edits: "allow",   strict: "confirm", dont_ask: "deny"    },

  // UNKNOWN 兜底 — 严格 fail-closed
  { klass: "unknown",          label: "未分类",     desc: "新工具未声明 ApprovalClass",     trust: "confirm", default: "confirm", accept_edits: "confirm", strict: "confirm", dont_ask: "deny"    },
];

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

export function PolicyV2MatrixView() {
  const { t } = useTranslation();
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
            {SESSION_ROLES.map((r) => (
              <div
                key={r.id}
                className="rounded-md border border-border/50 bg-muted/30 px-3 py-2"
              >
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant="outline" className="text-[10px] uppercase">{r.id}</Badge>
                  <span className="text-sm font-medium">{r.label}</span>
                </div>
                <div className="text-xs text-muted-foreground">{r.desc}</div>
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
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-border/50">
                  <th className="text-left py-2 pr-3 font-medium text-muted-foreground">
                    {t("security.matrixColClass", "ApprovalClass")}
                  </th>
                  {CONFIRMATION_MODES.map((m) => (
                    <th key={m.id} className="text-center py-2 px-2 font-medium">
                      <div className="text-sm">{m.label}</div>
                      <div className="text-[10px] text-muted-foreground font-normal mt-0.5">{m.desc}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {MATRIX.map((row) => (
                  <tr key={row.klass} className="border-b border-border/30 last:border-b-0">
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-2">
                        <code className="text-[10px] text-muted-foreground">{row.klass}</code>
                      </div>
                      <div className="text-[11px] mt-0.5"><strong>{row.label}</strong></div>
                      <div className="text-[10px] text-muted-foreground">{row.desc}</div>
                    </td>
                    <td className="text-center py-2 px-2"><DecisionCell decision={row.trust} /></td>
                    <td className="text-center py-2 px-2"><DecisionCell decision={row.default} /></td>
                    <td className="text-center py-2 px-2"><DecisionCell decision={row.accept_edits} /></td>
                    <td className="text-center py-2 px-2"><DecisionCell decision={row.strict} /></td>
                    <td className="text-center py-2 px-2"><DecisionCell decision={row.dont_ask} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

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
              "数据源：src/openakita/core/policy_v2/engine.py 决策链 + plan §3。如果你看到此处与实际行为不符，请在 GitHub 提 issue 附"+
                "audit JSONL（data/audit/policy_decisions.jsonl）。",
            )}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// 测试守卫 export — 后端测试可通过 grep 找到 MATRIX 常量并解析一致性
export const __POLICY_V2_MATRIX_FOR_TESTS = MATRIX;
export const __POLICY_V2_MODES_FOR_TESTS = CONFIRMATION_MODES;
export const __POLICY_V2_ROLES_FOR_TESTS = SESSION_ROLES;
