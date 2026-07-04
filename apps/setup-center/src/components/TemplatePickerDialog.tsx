/**
 * V2 organisation template picker — centered Modal dialog.
 *
 * Phase-6 entry point for the v2 "create org from template" flow.
 * Lists the templates returned by ``GET /api/v2/orgs/templates``
 * (gated by ``settings.runtime_v2_enabled`` on the backend), lets
 * the operator name the new org, and POSTs to
 * ``/api/v2/orgs/from-template``. The returned :class:`OrgWire`
 * is passed up to the caller via ``onCreated`` — the caller then
 * refreshes the sidebar and selects the new org.
 *
 * Replaces the previous side-drawer (TemplatePickerDrawer.tsx);
 * a centered modal works better here because each template card
 * benefits from horizontal space (display name + description +
 * node count) and the operator only needs the picker for a few
 * seconds before continuing inside the editor.
 *
 * UX contract:
 *   - Click trigger -> modal opens (Radix Dialog, focus trap, Esc to close, backdrop click closes).
 *   - First template auto-selected on first open; clicking another
 *     card switches the selection (visual ring + indigo bg).
 *   - "新组织名称" text input below the list.
 *   - Footer: 取消 / 创建组织.
 *   - 创建组织 disabled until BOTH a template is selected AND name.trim() is non-empty.
 *   - On success modal auto-closes and ``onCreated`` fires.
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

import {
  type OrgWire,
  type TemplateWire,
  instantiateTemplate,
  listTemplates,
} from "../api/orgs";

interface TemplatePickerDialogProps {
  /** ``httpApiBase()`` result — the v1/v2 base URL of the backend. */
  apiBase: string;
  /** Called after a successful instantiate. */
  onCreated: (org: OrgWire) => void;
  /** Optional custom trigger; if omitted a default button is rendered. */
  children?: React.ReactNode;
}

export function TemplatePickerDialog({
  apiBase,
  onCreated,
  children,
}: TemplatePickerDialogProps) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<TemplateWire[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await listTemplates(apiBase);
      // P2 去重：磁盘上可能残留旧 slug 的同名模板（如 ``content-ops`` 与
      // 历史 ``content_ops``/``内容运营团队`` 各一份），导致列表里"内容运营
      // 团队"出现两次。按显示名去重，优先保留 ASCII slug（连字符）的内置
      // 条目，避免用户看到重复模板。
      const byName = new Map<string, TemplateWire>();
      for (const tpl of resp) {
        const label = (tpl.name || tpl.display_name || tpl.id || "").trim();
        const existing = byName.get(label);
        if (!existing) {
          byName.set(label, tpl);
          continue;
        }
        // Prefer the hyphen-case built-in slug over legacy variants.
        const prefer = /^[a-z0-9-]+$/.test(tpl.id) && !/^[a-z0-9-]+$/.test(existing.id);
        if (prefer) byName.set(label, tpl);
      }
      const deduped = [...byName.values()];
      setTemplates(deduped);
      if (deduped.length > 0 && selectedId === null) {
        setSelectedId(deduped[0].id);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`无法加载组织模板：${msg}（灰度未启用时是正常的）`);
    } finally {
      setLoading(false);
    }
  }, [apiBase, selectedId]);

  useEffect(() => {
    if (open) {
      loadTemplates();
    } else {
      // Reset transient state when modal closes so the next open
      // starts clean (selection + name).
      setName("");
      setError(null);
      setSelectedId(null);
    }
  }, [open, loadTemplates]);

  const handleSubmit = useCallback(async () => {
    if (!selectedId || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const org = await instantiateTemplate(apiBase, selectedId, { name: name.trim() });
      onCreated(org);
      setOpen(false);
      setName("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`实例化失败：${msg}`);
    } finally {
      setSubmitting(false);
    }
  }, [apiBase, selectedId, name, onCreated]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {children ?? <Button variant="outline">新建组织</Button>}
      </DialogTrigger>
      <DialogContent className="sm:max-w-[560px]" data-testid="v2-template-dialog">
        <DialogHeader>
          <DialogTitle>选择组织模板</DialogTitle>
          <DialogDescription>
            从内置模板克隆一份新的组织。点选模板，输入组织名称后即可创建。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>加载模板中…</span>
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="text-sm text-destructive border border-destructive/40 rounded-md p-2"
            >
              {error}
            </div>
          )}

          {!loading && templates.length === 0 && !error && (
            <div className="text-sm text-muted-foreground">
              暂无可用模板。请确认后端已启用 ``settings.runtime_v2_enabled``。
            </div>
          )}

          {templates.length > 0 && (
            <ul
              className="space-y-2 max-h-[42vh] overflow-y-auto px-1 py-1"
              data-testid="v2-template-dialog-list"
            >
              {templates.map((t) => {
                const isSelected = selectedId === t.id;
                return (
                  <li key={t.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(t.id)}
                      data-testid={`v2-template-card-${t.id}`}
                      data-selected={isSelected ? "true" : "false"}
                      className={
                        "w-full text-left rounded-md border px-3 py-2 transition " +
                        (isSelected
                          ? "border-indigo-500 ring-2 ring-indigo-500 bg-indigo-50 dark:bg-indigo-950/30"
                          : "border-border hover:bg-muted/50")
                      }
                    >
                      <div className="font-medium text-sm flex items-center gap-2">
                        <span>{t.name || t.id}</span>
                        {isSelected && (
                          <span className="text-[10px] font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-300">
                            已选中
                          </span>
                        )}
                      </div>
                      {t.description && (
                        <div className="text-xs text-muted-foreground mt-1 line-clamp-2">
                          {t.description}
                        </div>
                      )}
                      <div className="text-[11px] text-muted-foreground mt-1">
                        节点 {t.node_count}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="space-y-1">
            <label className="text-xs font-medium" htmlFor="tpd-org-name">
              新组织名称
            </label>
            <input
              id="tpd-org-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：Acme 编辑部"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost" disabled={submitting}>
              取消
            </Button>
          </DialogClose>
          <Button
            onClick={handleSubmit}
            disabled={!selectedId || !name.trim() || submitting}
            data-testid="v2-template-dialog-create"
          >
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            创建组织
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default TemplatePickerDialog;