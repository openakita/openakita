import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import {
  IconRefresh, IconCheck, IconX, IconInfo,
  IconChevronRight, IconLoader, IconFingerprint,
} from "../icons";
import { ModalOverlay } from "../components/ModalOverlay";

type IdentityFile = {
  name: string;
  exists: boolean;
  restricted: boolean;
  is_runtime: boolean;
  warning_key: string | null;
  budget_tokens: number | null;
  size?: number;
  modified?: string;
  tokens?: number;
};

type Props = {
  serviceRunning: boolean;
  apiBaseUrl: string;
};

const MEMORY_MAX_CHARS = 1500;

function estimateTokens(text: string): number {
  if (!text) return 0;
  let cn = 0;
  for (const c of text) {
    if (c >= "\u4e00" && c <= "\u9fff") cn++;
  }
  const en = text.length - cn;
  return Math.round(cn / 1.5 + en / 4);
}

function diffLines(original: string, modified: string): { added: number; removed: number } {
  const a = original.split("\n");
  const b = modified.split("\n");
  const setA = new Set(a);
  const setB = new Set(b);
  let added = 0, removed = 0;
  for (const line of b) if (!setA.has(line)) added++;
  for (const line of a) if (!setB.has(line)) removed++;
  return { added, removed };
}

const WARNING_KEYS: Record<string, string> = {
  "SOUL.md": "soulWarning",
  "AGENT.md": "agentWarning",
  "USER.md": "userWarning",
  "MEMORY.md": "memoryWarning",
  "POLICIES.yaml": "policiesYamlWarning",
  "prompts/policies.md": "policiesMdWarning",
};

const SOURCE_TO_RUNTIME: Record<string, string[]> = {
  "AGENT.md": ["runtime/agent.core.md", "runtime/agent.tooling.md"],
  "USER.md": ["runtime/user.summary.md"],
};

export function IdentityView({ serviceRunning, apiBaseUrl }: Props) {
  const API = apiBaseUrl;
  const { t } = useTranslation();

  const [files, setFiles] = useState<IdentityFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [compiling, setCompiling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{
    title: string;
    body: string;
    warnings?: string[];
    onConfirm: () => void;
  } | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(() => {
    try { return localStorage.getItem("identity_banner_dismissed") === "1"; } catch { return false; }
  });
  const [fileWarningExpanded, setFileWarningExpanded] = useState(false);

  const editorRef = useRef<HTMLTextAreaElement>(null);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  }, []);

  // Load file list
  const loadFiles = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${API}/api/identity/files`);
      const data = await res.json();
      setFiles(data.files || []);
    } catch (e) {
      setError(String(e));
    }
  }, [API, serviceRunning]);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  // Load file content
  const loadFile = useCallback(async (name: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await safeFetch(`${API}/api/identity/file?name=${encodeURIComponent(name)}`);
      const data = await res.json();
      setContent(data.content || "");
      setOriginalContent(data.content || "");
      setSelectedFile(name);
      setFileWarningExpanded(false);
    } catch (e) {
      setError(t("identity.loadError") + ": " + String(e));
    } finally {
      setLoading(false);
    }
  }, [API, t]);

  // Save with validation: validate first, then write
  const handleSave = useCallback(async (force = false) => {
    if (!selectedFile) return;
    setSaving(true);
    setError(null);
    try {
      // Step 1: Validate (always returns 200)
      if (!force) {
        const valRes = await safeFetch(`${API}/api/identity/validate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: selectedFile, content }),
        });
        const valData = await valRes.json();

        if (valData.errors && valData.errors.length > 0) {
          setError(valData.errors.join("\n"));
          return;
        }

        if (valData.warnings && valData.warnings.length > 0) {
          const diff = diffLines(originalContent, content);
          const isHighRisk = selectedFile === "SOUL.md" || selectedFile === "AGENT.md";
          setConfirmDialog({
            title: t("identity.confirmTitle"),
            body: [
              t("identity.confirmChangesSummary", { added: diff.added, removed: diff.removed }),
              ...valData.warnings,
              isHighRisk ? t("identity.confirmRiskSoulAgent") : "",
            ].filter(Boolean).join("\n\n"),
            warnings: valData.warnings,
            onConfirm: () => {
              setConfirmDialog(null);
              handleSave(true);
            },
          });
          return;
        }
      }

      // Step 2: Write (force=true skips server-side warning check)
      const res = await safeFetch(`${API}/api/identity/file`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: selectedFile, content, force: true }),
      });
      const data = await res.json();

      if (data.saved) {
        setOriginalContent(content);
        // Auto-reload identity
        try {
          await safeFetch(`${API}/api/identity/reload`, { method: "POST" });
        } catch { /* best effort */ }
        showToast(t("identity.saved"));
        loadFiles();
      }
    } catch (e) {
      setError(t("identity.saveError") + ": " + String(e));
    } finally {
      setSaving(false);
    }
  }, [API, selectedFile, content, originalContent, t, showToast, loadFiles]);

  // Compile
  const handleCompile = useCallback(async (mode: "llm" | "rules") => {
    setCompiling(true);
    setError(null);
    try {
      const res = await safeFetch(`${API}/api/identity/compile?mode=${mode}`, {
        method: "POST",
        signal: AbortSignal.timeout(60_000),
      });
      const data = await res.json();
      showToast(`${t("identity.compiled")} (${data.mode_used})`);
      loadFiles();
      // If currently viewing a runtime file, reload it
      if (selectedFile?.startsWith("runtime/")) {
        loadFile(selectedFile);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setCompiling(false);
    }
  }, [API, t, showToast, loadFiles, selectedFile, loadFile]);

  // Reload identity
  const handleReload = useCallback(async () => {
    try {
      await safeFetch(`${API}/api/identity/reload`, { method: "POST" });
      showToast(t("identity.reloadSuccess"));
    } catch (e) {
      setError(t("identity.reloadError") + ": " + String(e));
    }
  }, [API, t, showToast]);

  const hasChanges = content !== originalContent;
  const currentMeta = files.find(f => f.name === selectedFile);
  const warningKey = selectedFile ? (WARNING_KEYS[selectedFile] || (selectedFile.startsWith("runtime/") ? "runtimeWarning" : null)) : null;
  const tokenCount = estimateTokens(content);
  const budgetTokens = currentMeta?.budget_tokens;

  const sourceFiles = files.filter(f => !f.is_runtime);
  const runtimeFiles = files.filter(f => f.is_runtime);

  const runtimeLinks = selectedFile ? SOURCE_TO_RUNTIME[selectedFile] : undefined;

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconFingerprint size={48} />
        <div className="mt-3 font-semibold">{t("identity.title")}</div>
        <div className="mt-1 text-xs opacity-50">后端服务未启动，请启动后再进行使用</div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0, height: "100%" }}>
      {/* Page-level warning banner (dismissible) */}
      {!bannerDismissed && (
        <div style={{
          background: "var(--warning-bg, #fef3c7)",
          border: "1px solid var(--warning-border, #f59e0b)",
          borderRadius: 6,
          padding: "6px 12px",
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 12,
          color: "var(--warning-text, #92400e)",
          lineHeight: 1.4,
        }}>
          <IconInfo size={14} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{t("identity.pageBanner")}</span>
          <span style={{ flexShrink: 0, cursor: "pointer", opacity: 0.6, display: "inline-flex" }} onClick={() => {
            setBannerDismissed(true);
            try { localStorage.setItem("identity_banner_dismissed", "1"); } catch { /* ignore */ }
          }}><IconX size={14} /></span>
        </div>
      )}

      <div className="identityLayout" style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
        {/* Left: File list */}
        <div className="identityFileList" style={{
          width: 220,
          flexShrink: 0,
          overflowY: "auto",
          borderRight: "1px solid var(--border, #e2e8f0)",
          paddingRight: 10,
        }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", padding: "4px 0 6px", letterSpacing: 0.5 }}>
            {t("identity.sourceFiles")}
          </div>
          {sourceFiles.map(f => (
            <FileItem key={f.name} file={f} selected={selectedFile === f.name} onClick={() => loadFile(f.name)} />
          ))}

          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", padding: "10px 0 6px", letterSpacing: 0.5 }}>
            {t("identity.runtimeFiles")}
          </div>
          {runtimeFiles.map(f => (
            <FileItem key={f.name} file={f} selected={selectedFile === f.name} onClick={() => loadFile(f.name)} />
          ))}
        </div>

        {/* Right: Editor */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {!selectedFile ? (
            <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
              {t("identity.noFileSelected")}
            </div>
          ) : (
            <>
              {/* Toolbar */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600, fontSize: 14, flex: 1 }}>{selectedFile}</span>

                {runtimeLinks && (
                  <span style={{ fontSize: 12, color: "#64748b" }}>
                    → {runtimeLinks.map((r, i) => (
                      <span key={r}>
                        {i > 0 && ", "}
                        <a href="#" onClick={e => { e.preventDefault(); loadFile(r); }} style={{ color: "var(--primary, #3b82f6)" }}>{r}</a>
                      </span>
                    ))}
                  </span>
                )}

                {selectedFile === "SOUL.md" && (
                  <span style={{ fontSize: 11, color: "var(--ok, #059669)", background: "var(--ok-bg, #d1fae5)", padding: "2px 8px", borderRadius: 4 }}>
                    {t("identity.fullTextInject")}
                  </span>
                )}
                {selectedFile === "AGENT.md" && (
                  <span style={{ fontSize: 11, color: "var(--warn, #d97706)", background: "var(--warn-bg, #fef3c7)", padding: "2px 8px", borderRadius: 4 }}>
                    {t("identity.needsCompile")}
                  </span>
                )}

                <button className="btnSmall" onClick={handleReload} title={t("identity.reload")} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <IconRefresh size={14} /> {t("identity.reload")}
                </button>
                <button className="btnSmall" onClick={() => handleCompile("llm")} disabled={compiling} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  {compiling ? <IconLoader size={14} /> : <IconCheck size={14} />}
                  {compiling ? t("identity.compiling") : t("identity.compile")}
                </button>
                <button className="btnSmall" onClick={() => handleCompile("rules")} disabled={compiling} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  {t("identity.compileRules")}
                </button>
                <button
                  className="btnPrimary btnSmall"
                  onClick={() => handleSave(false)}
                  disabled={saving || !hasChanges}
                  style={{ display: "flex", alignItems: "center", gap: 4 }}
                >
                  {saving ? <IconLoader size={14} /> : <IconCheck size={14} />}
                  {t("identity.save")}
                </button>
              </div>

              {/* File-level warning (compact, expandable) */}
              {warningKey && (
                <div
                  style={{
                    background: "var(--warning-bg, #fef3c7)",
                    border: "1px solid var(--warning-border, #f59e0b)",
                    borderRadius: 5,
                    padding: "4px 10px",
                    marginBottom: 6,
                    fontSize: 11.5,
                    color: "var(--warning-text, #92400e)",
                    lineHeight: 1.4,
                    cursor: "pointer",
                  }}
                  onClick={() => setFileWarningExpanded(v => !v)}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <IconInfo size={12} style={{ flexShrink: 0 }} />
                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: fileWarningExpanded ? "normal" : "nowrap" }}>
                      {t(`identity.${warningKey}`)}
                    </span>
                    {!fileWarningExpanded && <span style={{ flexShrink: 0, opacity: 0.5, fontSize: 10 }}>▼</span>}
                  </div>
                </div>
              )}

              {/* Error banner */}
              {error && (
                <div style={{
                  background: "#fef2f2",
                  border: "1px solid #fca5a5",
                  borderRadius: 6,
                  padding: "8px 12px",
                  marginBottom: 8,
                  fontSize: 12,
                  color: "#991b1b",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                }}>
                  <span style={{ flexShrink: 0, marginTop: 2, cursor: "pointer", display: "inline-flex" }} onClick={() => setError(null)}><IconX size={14} /></span>
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{error}</pre>
                </div>
              )}

              {/* Editor */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
                {loading ? (
                  <div style={{ display: "flex", justifyContent: "center", alignItems: "center", flex: 1 }}>
                    <IconLoader size={24} />
                  </div>
                ) : (
                  <textarea
                    ref={editorRef}
                    className="input"
                    value={content}
                    onChange={e => setContent(e.target.value)}
                    style={{
                      flex: 1,
                      resize: "none",
                      fontFamily: "monospace",
                      fontSize: 12.5,
                      lineHeight: 1.6,
                      padding: 12,
                      minHeight: 300,
                    }}
                    spellCheck={false}
                  />
                )}
              </div>

              {/* Status bar */}
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "6px 2px",
                fontSize: 11,
                color: "#64748b",
              }}>
                <span>
                  {selectedFile === "MEMORY.md" && (
                    <span style={{ color: content.length > MEMORY_MAX_CHARS ? "#dc2626" : undefined, fontWeight: content.length > MEMORY_MAX_CHARS ? 600 : undefined }}>
                      {t("identity.charCount", { count: content.length, max: MEMORY_MAX_CHARS })}
                      {content.length > MEMORY_MAX_CHARS && ` — ${t("identity.charCountExceeded")}`}
                    </span>
                  )}
                </span>
                <span style={{ color: budgetTokens && tokenCount > budgetTokens ? "#dc2626" : undefined }}>
                  {budgetTokens
                    ? t("identity.tokenCount", { count: tokenCount, budget: budgetTokens })
                    : `~${tokenCount} tokens`
                  }
                  {budgetTokens && tokenCount > budgetTokens && ` — ${t("identity.tokenOverBudget")}`}
                </span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Confirm dialog */}
      {confirmDialog && (
        <ModalOverlay onClose={() => setConfirmDialog(null)} className="" style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex",
          justifyContent: "center", alignItems: "center", zIndex: 9999,
        }}>
          <div style={{
            background: "var(--card-bg, #fff)", borderRadius: 12, padding: 24,
            maxWidth: 480, width: "90%", boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
          }}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 12, color: "#dc2626" }}>
              {confirmDialog.title}
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.7, color: "var(--text-secondary, #475569)", whiteSpace: "pre-wrap", marginBottom: 16 }}>
              {confirmDialog.body}
            </div>
            {confirmDialog.warnings && confirmDialog.warnings.length > 0 && (
              <ul style={{ margin: "0 0 16px", padding: "0 0 0 20px", fontSize: 13, color: "#d97706" }}>
                {confirmDialog.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btnSmall" onClick={() => setConfirmDialog(null)} autoFocus>
                {t("identity.confirmCancel")}
              </button>
              <button className="btnPrimary btnSmall" onClick={confirmDialog.onConfirm} style={{ background: "#dc2626" }}>
                {t("identity.confirmSave")}
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Toast */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          background: "#059669", color: "#fff", padding: "8px 20px", borderRadius: 8,
          fontSize: 13, fontWeight: 500, zIndex: 9999,
          boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────

function FileItem({ file, selected, onClick }: { file: IdentityFile; selected: boolean; onClick: () => void }) {
  const displayName = file.name.startsWith("runtime/")
    ? file.name.replace("runtime/", "")
    : file.name.startsWith("personas/")
    ? file.name.replace("personas/", "👤 ")
    : file.name;

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      style={{
        padding: "5px 8px",
        borderRadius: 5,
        cursor: "pointer",
        fontSize: 12.5,
        display: "flex",
        alignItems: "center",
        gap: 4,
        background: selected ? "var(--primary-alpha, rgba(59,130,246,0.1))" : "transparent",
        fontWeight: selected ? 600 : 400,
        color: selected ? "var(--primary, #3b82f6)" : file.exists ? "var(--text, #334155)" : "#94a3b8",
        opacity: file.exists ? 1 : 0.5,
      }}
      onKeyDown={e => e.key === "Enter" && onClick()}
    >
      <IconChevronRight size={10} />
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {displayName}
      </span>
      {file.restricted && (
        <span title="Restricted" style={{ color: "#f59e0b", fontSize: 10 }}>⚠</span>
      )}
      {file.is_runtime && (
        <span title="Compiled" style={{ color: "#8b5cf6", fontSize: 9 }}>RT</span>
      )}
    </div>
  );
}
