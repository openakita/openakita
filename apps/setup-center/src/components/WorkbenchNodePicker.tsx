/**
 * WorkbenchNodePicker
 * ────────────────────────────────────────────────────────────────
 * 在组织编排器中弹出一个选择框，列出后端通过
 * `GET /api/orgs/plugin-workbench-templates` 返回的"工作台模板"。
 * 每个模板对应一个已加载并注册了 LLM 工具的工作台应用，点击后回调上层用
 * `template.suggested_node` 在画布上创建一个预配置的叶子节点
 * （`external_tools` 已锁定为该工作台的工具集，`plugin_origin` 写入
 * 节点 data 以便后续 UI / 提示词 / 运行时识别）。
 *
 * 关键约束（与后端 OrgManager.update / OrgRuntime._create_node_agent 对齐）：
 *   - 工作台节点必须是叶子节点（不允许挂下属），否则保存被拒绝
 *   - 工作台工具列表由工作台应用决定，UI 上以只读形式展示，避免用户误删
 */
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { IconX, IconPlug } from "../icons";

export interface WorkbenchTemplateToolDef {
  name: string;
  description?: string;
  input_schema?: Record<string, unknown>;
}

export interface WorkbenchSuggestedNode {
  role_title?: string;
  role_goal?: string;
  custom_prompt?: string;
  external_tools?: string[];
  agent_profile_id?: string | null;
  enable_file_tools?: boolean;
  mcp_servers?: string[];
  skills?: string[];
  skills_mode?: string;
  max_concurrent_tasks?: number;
  can_delegate?: boolean;
  can_escalate?: boolean;
  plugin_origin?: {
    plugin_id: string;
    template_id: string;
    version?: string;
  };
}

export interface WorkbenchTemplate {
  id: string;
  plugin_id: string;
  version?: string;
  name: string;
  name_i18n?: { zh?: string; en?: string };
  description?: string;
  description_i18n?: Record<string, string>;
  icon?: string;
  category?: string;
  tools: WorkbenchTemplateToolDef[];
  tool_names?: string[];
  suggested_node: WorkbenchSuggestedNode;
}

export interface WorkbenchNodePickerProps {
  apiBaseUrl: string;
  open: boolean;
  onClose: () => void;
  onPick: (tpl: WorkbenchTemplate) => void;
}

export function WorkbenchNodePicker(props: WorkbenchNodePickerProps) {
  const { apiBaseUrl, open, onClose, onPick } = props;
  const { t, i18n } = useTranslation();
  const [items, setItems] = useState<WorkbenchTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/plugin-workbench-templates`);
        const data = await res.json();
        if (cancelled) return;
        setItems(Array.isArray(data) ? data : []);
      } catch (e) {
        if (cancelled) return;
        setError((e as Error)?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, open]);

  const isZh = (i18n.language || "").toLowerCase().startsWith("zh");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter((tpl) => {
      const hay = [
        tpl.name,
        tpl.name_i18n?.zh,
        tpl.name_i18n?.en,
        tpl.description,
        tpl.plugin_id,
        tpl.category,
        ...(tpl.tool_names || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [items, search]);

  if (!open) return null;

  return createPortal(
    <div
      className="org-modal-overlay"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="org-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 720, maxHeight: "82vh", display: "flex", flexDirection: "column" }}
        role="dialog"
        aria-modal="true"
        aria-label={t("org.editor.workbenchPickerTitle", "选择工作台")}
      >
        <div className="org-modal-header">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <IconPlug size={14} />
            {t("org.editor.workbenchPickerTitle", "选择工作台")}
          </span>
          <button className="org-modal-close" onClick={onClose} aria-label={t("org.editor.close", "关闭")}>
            <IconX size={14} />
          </button>
        </div>
        <div style={{ padding: "8px 14px 0 14px" }}>
          <input
            type="text"
            value={search}
            placeholder={t("org.editor.workbenchSearchPh", "搜索工作台名称、工作台应用 ID 或工具名…")}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: "100%",
              padding: "6px 10px",
              borderRadius: 4,
              border: "1px solid var(--line)",
              background: "var(--card-bg, #fff)",
              fontSize: 12,
              boxSizing: "border-box",
            }}
          />
        </div>
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: 14,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 10,
            alignContent: "start",
          }}
        >
          {loading && (
            <div style={{ gridColumn: "1 / -1", textAlign: "center", color: "var(--muted)", fontSize: 12, padding: 20 }}>
              {t("org.editor.loading", "加载中…")}
            </div>
          )}
          {!loading && error && (
            <div style={{ gridColumn: "1 / -1", color: "var(--danger, #ef4444)", fontSize: 12, padding: 8 }}>
              {t("org.editor.workbenchLoadFailed", "加载工作台失败")}：{error}
            </div>
          )}
          {!loading && !error && filtered.length === 0 && (
            <div
              style={{
                gridColumn: "1 / -1",
                color: "var(--muted)",
                fontSize: 12,
                padding: 20,
                textAlign: "center",
              }}
            >
              {items.length === 0
                ? t(
                    "org.editor.workbenchEmpty",
                    "暂无可用工作台：请先在「工作台」中加载提供 LLM 工具的工作台应用。",
                  )
                : t("org.editor.workbenchNoMatch", "没有匹配的工作台。")}
            </div>
          )}
          {filtered.map((tpl) => {
            const displayName =
              (isZh ? tpl.name_i18n?.zh : tpl.name_i18n?.en) || tpl.name || tpl.plugin_id;
            const desc =
              (isZh ? tpl.description_i18n?.zh : tpl.description_i18n?.en) ||
              tpl.description ||
              "";
            return (
              <button
                key={tpl.id}
                onClick={() => {
                  onPick(tpl);
                  onClose();
                }}
                style={{
                  textAlign: "left",
                  border: "1px solid var(--line)",
                  borderRadius: 6,
                  background: "var(--card-bg, #fff)",
                  padding: 10,
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  transition: "border-color .15s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "var(--primary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--line)";
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: 4,
                      background: "#ecfeff",
                      color: "#0e7490",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 13,
                    }}
                    aria-hidden="true"
                  >
                    {tpl.icon && tpl.icon.length <= 4 ? (
                      <span>{tpl.icon}</span>
                    ) : (
                      <IconPlug size={14} />
                    )}
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {displayName}
                    </div>
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--muted)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {tpl.plugin_id}
                      {tpl.version ? ` · v${tpl.version}` : ""}
                      {tpl.category ? ` · ${tpl.category}` : ""}
                    </div>
                  </div>
                </div>
                {desc && (
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--muted)",
                      lineHeight: 1.5,
                      maxHeight: 48,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                    }}
                  >
                    {desc}
                  </div>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {(tpl.tools || []).slice(0, 6).map((tool) => (
                    <span
                      key={tool.name}
                      title={tool.description || tool.name}
                      style={{
                        fontSize: 9,
                        padding: "1px 5px",
                        borderRadius: 3,
                        background: "var(--bg-subtle, #f3f4f6)",
                        color: "var(--muted)",
                        fontFamily: "var(--font-mono, monospace)",
                      }}
                    >
                      {tool.name}
                    </span>
                  ))}
                  {(tpl.tools || []).length > 6 && (
                    <span style={{ fontSize: 9, color: "var(--muted)" }}>
                      +{(tpl.tools || []).length - 6}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
        <div className="org-modal-footer">
          <button className="org-modal-btn" onClick={onClose}>
            {t("org.editor.cancel", "取消")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default WorkbenchNodePicker;
