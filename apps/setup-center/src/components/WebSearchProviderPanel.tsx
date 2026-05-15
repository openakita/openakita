// WebSearchProviderPanel — settings card for picking + configuring the
// active web_search provider (Bocha / Tavily / SearXNG / Jina / DuckDuckGo).
//
// Lives inside the "工具与技能" page as a <details> child mounted by App.tsx.
// Why a separate component instead of inline JSX in App.tsx? App.tsx is
// already 5K+ lines; pulling provider-specific UI out keeps it grep-able and
// lets the component own its `/api/tools/web-search/*` calls directly.
//
// Data flow:
//   1. On mount: GET /api/tools/web-search/providers → list of provider
//      descriptors (id/label/is_available/auto_detect_order/etc.).
//   2. User edits keys via existing FieldText components (which mutate
//      ``envDraft`` — actual save happens via ``onSaveEnv`` button so we
//      don't ping the .env file on every keystroke).
//   3. User clicks "测试" → POST /api/tools/web-search/test → renders
//      structured result with the same ConfigHintErrorCode classification
//      as the chat-side ConfigHintCard for consistency.

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { FieldText, FieldLabel } from "./EnvFields";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
  SelectValue,
} from "@/components/ui/select";
import { safeFetch } from "../providers";
import { envGet, envSet } from "../utils";
import type { EnvMap } from "../types";

// Radix Select forbids ``value=""`` on Select.Item (it's reserved for
// "clear selection"). Our backend treats an empty ``WEB_SEARCH_PROVIDER`` as
// "auto-detect", so we use this UI-only sentinel and translate at the
// envDraft boundary. Keep it impossible to collide with any real provider id.
const AUTO_DETECT_SENTINEL = "__auto__";

// ---- Types mirroring src/openakita/api/routes/web_search.py response shape ----

interface ProviderDescriptor {
  id: string;
  label: string;
  requires_credential: boolean;
  is_available: boolean;
  auto_detect_order: number;
  signup_url: string;
  docs_url: string;
}

interface ProvidersResponse {
  active: string;
  providers: ProviderDescriptor[];
}

interface TestSearchResultItem {
  title: string;
  url: string;
  snippet?: string;
}

interface TestSearchResponse {
  ok: boolean;
  provider_id: string;
  results?: TestSearchResultItem[];
  error_code?: string;
  message?: string;
}

// Per-provider UI metadata: which env key holds the credential, recommended
// flag (gold badge), and signup CTA label. Kept in component-local config so
// adding a new provider is a single entry here + a new module under
// ``src/openakita/tools/web_search/providers/``.
interface ProviderUIConfig {
  envKey: string;          // .env key bound to the credential
  envType?: "text" | "password";
  envPlaceholder?: string;
  envHelp?: string;
  recommended?: boolean;
}

const PROVIDER_UI: Record<string, ProviderUIConfig> = {
  bocha: {
    envKey: "BOCHA_API_KEY",
    envType: "password",
    envPlaceholder: "sk-...",
    recommended: true, // 国内推荐
  },
  tavily: {
    envKey: "TAVILY_API_KEY",
    envType: "password",
    envPlaceholder: "tvly-...",
    recommended: true, // 海外推荐
  },
  searxng: {
    envKey: "SEARXNG_BASE_URL",
    envType: "text",
    envPlaceholder: "http://localhost:8080",
  },
  jina: {
    envKey: "JINA_API_KEY",
    envType: "password",
    envPlaceholder: "jina_... (可选)",
  },
  duckduckgo: {
    envKey: "",  // no credential
  },
};

interface WebSearchProviderPanelProps {
  envDraft: EnvMap;
  onEnvChange: (updater: (prev: EnvMap) => EnvMap) => void;
  onSaveEnv: () => Promise<void> | void;
  busy?: string | null;
  // Backend base URL (e.g. ``http://127.0.0.1:18900``). Required because in
  // Tauri/web modes the webview origin (5173 / custom scheme) is NOT the
  // backend origin, so a relative ``/api/...`` would be served the SPA's
  // index.html — yielding the classic
  // ``Unexpected token '<', "<!doctype "...`` JSON parse error.
  apiBaseUrl: string;
}

export default function WebSearchProviderPanel({
  envDraft,
  onEnvChange,
  onSaveEnv,
  busy,
  apiBaseUrl,
}: WebSearchProviderPanelProps) {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [active, setActive] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string>("");
  // Per-provider test result, keyed by provider id; rendered inline.
  const [testResults, setTestResults] = useState<Record<string, TestSearchResponse>>({});
  const [testingId, setTestingId] = useState<string>("");

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const r = await safeFetch(`${apiBaseUrl}/api/tools/web-search/providers`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Defense-in-depth: when the dev server (or a misconfigured proxy)
      // serves index.html instead of the JSON endpoint we'd otherwise crash
      // inside ``r.json()`` with the cryptic ``Unexpected token '<'`` error.
      // Sniffing content-type lets us surface a useful diagnostic instead.
      const ct = r.headers.get("content-type") || "";
      if (!ct.includes("json")) {
        throw new Error(
          `后端返回非 JSON（content-type=${ct || "未知"}）。请确认服务已启动且 apiBaseUrl 配置正确。`,
        );
      }
      const data = (await r.json()) as ProvidersResponse;
      setProviders(data.providers || []);
      setActive(data.active || "");
    } catch (err) {
      setLoadError(String(err));
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleTest = useCallback(
    async (providerId: string) => {
      setTestingId(providerId);

      // ── Phase 1: persist the latest env draft so the backend reads the key
      // the user just typed. We deliberately separate this from the test call
      // so a *save* failure (workspace not selected, write-permission denied,
      // network glitch when proxying to a remote backend) is reported with
      // its own error_code instead of being attributed to the provider's
      // search endpoint — which used to confuse users into thinking their
      // valid key was wrong.
      try {
        await onSaveEnv();
      } catch (saveErr) {
        setTestResults((prev) => ({
          ...prev,
          [providerId]: {
            ok: false,
            provider_id: providerId,
            // Distinct sentinel — UI can branch on it later if we want a
            // different copy ("配置保存失败" vs "搜索源测试失败").
            error_code: "save_failed",
            message: `保存配置失败：${String(saveErr)}。请检查工作区是否已选中、是否有写权限，再重试。`,
          },
        }));
        setTestingId("");
        return;
      }

      // ── Phase 2: actual provider call. Network/JSON failures here are the
      // backend's responsibility (the /test endpoint already returns a
      // structured ConfigHintErrorCode), so we only need a generic ``unknown``
      // fallback for transport-level errors before the response is parsed.
      try {
        const r = await safeFetch(`${apiBaseUrl}/api/tools/web-search/test`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider_id: providerId,
            query: "OpenAkita",
            max_results: 3,
            timeout_seconds: 15,
          }),
        });
        const ct = r.headers.get("content-type") || "";
        if (!ct.includes("json")) {
          throw new Error(
            `后端返回非 JSON（content-type=${ct || "未知"}）`,
          );
        }
        const data = (await r.json()) as TestSearchResponse;
        setTestResults((prev) => ({ ...prev, [providerId]: data }));
      } catch (err) {
        setTestResults((prev) => ({
          ...prev,
          [providerId]: {
            ok: false,
            provider_id: providerId,
            error_code: "unknown",
            message: String(err),
          },
        }));
      } finally {
        setTestingId("");
        // Refresh availability after the test (so the "可用 / 未配置" badge
        // updates without requiring a manual reload).
        void reload();
      }
    },
    [onSaveEnv, reload, apiBaseUrl],
  );

  // Provider id selector — the global ``WEB_SEARCH_PROVIDER`` env value
  // controls auto-detect vs explicit pick. We intentionally do NOT reuse
  // ``FieldSelect`` here because Radix Select forbids empty-string values
  // on Item, and the backend semantics ("empty = auto-detect") would
  // require a translation layer the shared field can't express. Roll our
  // own inline Select with a sentinel + bidirectional mapping.
  const rawProvider = envGet(envDraft, "WEB_SEARCH_PROVIDER");
  const knownProviderIds = new Set(providers.map((p) => p.id));
  // If the env value is empty OR points at a provider we don't recognize,
  // show auto-detect. Last clause defends against stale ``.env`` lines from
  // a prior install with different providers registered.
  const selectValue =
    rawProvider && knownProviderIds.has(rawProvider)
      ? rawProvider
      : AUTO_DETECT_SENTINEL;

  const handleProviderChange = useCallback(
    (next: string) => {
      // Translate sentinel back to empty string before persisting — the
      // backend uses "" as the canonical "auto-detect" marker.
      const envValue = next === AUTO_DETECT_SENTINEL ? "" : next;
      onEnvChange((m) => envSet(m, "WEB_SEARCH_PROVIDER", envValue));
    },
    [onEnvChange],
  );

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted-foreground leading-relaxed">
        {t(
          "toolsWebSearch.intro",
          "DuckDuckGo 在国内常无法访问，建议配置博查（国内）或 Tavily（海外）等替代源。留空「激活源」走自动检测，按优先级（博查 → Tavily → SearXNG → Jina → DuckDuckGo）尝试已配置的可用源。",
        )}
      </div>

      <div className="space-y-1.5">
        <FieldLabel
          label={t("toolsWebSearch.activeProvider", "激活搜索源")}
          help={t(
            "toolsWebSearch.activeProviderHelp",
            "留空 = 按优先级自动检测；显式选择某个源时不再 fallback。",
          )}
          envKey="WEB_SEARCH_PROVIDER"
          htmlFor="ws-active-provider"
        />
        <Select
          value={selectValue}
          onValueChange={handleProviderChange}
          disabled={!!busy}
        >
          <SelectTrigger id="ws-active-provider" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={AUTO_DETECT_SENTINEL}>
              {t("toolsWebSearch.autoDetect", "自动检测（推荐）")}
            </SelectItem>
            {providers.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {loading && (
        <div className="text-xs text-muted-foreground">
          {t("common.loading", "加载中...")}
        </div>
      )}

      {loadError && (
        <div className="text-xs text-red-500">
          {t("toolsWebSearch.loadError", "加载搜索源列表失败：")}{loadError}
        </div>
      )}

      {!loading && !loadError && (
        <div className="wsProviderGrid">
          {providers.map((p) => {
            const ui = PROVIDER_UI[p.id] || {};
            const isActive = p.id === active;
            const test = testResults[p.id];
            return (
              <div
                key={p.id}
                className="wsProviderCard"
                data-active={isActive}
                data-available={p.is_available}
              >
                <div className="wsProviderHeader">
                  <div className="wsProviderTitle">
                    <span>{p.label}</span>
                  </div>
                  <div className="wsProviderBadges">
                    {isActive && (
                      <span className="wsBadge wsBadge-active">
                        {t("toolsWebSearch.badgeActive", "已激活")}
                      </span>
                    )}
                    {ui.recommended && (
                      <span className="wsBadge wsBadge-recommended">
                        {t("toolsWebSearch.badgeRecommended", "推荐")}
                      </span>
                    )}
                    {p.is_available ? (
                      <span className="wsBadge wsBadge-available">
                        {t("toolsWebSearch.badgeAvailable", "可用")}
                      </span>
                    ) : (
                      <span className="wsBadge wsBadge-unconfigured">
                        {t("toolsWebSearch.badgeUnconfigured", "未配置")}
                      </span>
                    )}
                  </div>
                </div>

                {ui.envKey ? (
                  <FieldText
                    k={ui.envKey}
                    label={ui.envKey}
                    placeholder={ui.envPlaceholder}
                    type={ui.envType || "password"}
                    help={ui.envHelp}
                    envDraft={envDraft}
                    onEnvChange={onEnvChange}
                    busy={busy}
                  />
                ) : (
                  <div className="text-xs text-muted-foreground">
                    {t(
                      "toolsWebSearch.noKeyNeeded",
                      "无需 API Key（依赖 ddgs Python 包；国内通常不可达）",
                    )}
                  </div>
                )}

                <div className="wsProviderActions">
                  {ui.envKey && (
                    <Button
                      variant="default"
                      size="sm"
                      disabled={!!testingId}
                      onClick={() => void handleTest(p.id)}
                    >
                      {testingId === p.id
                        ? t("toolsWebSearch.testing", "测试中...")
                        : t("toolsWebSearch.test", "测试")}
                    </Button>
                  )}
                  {p.signup_url && (
                    <a
                      href={p.signup_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                    >
                      {t("toolsWebSearch.signup", "申请 Key →")}
                    </a>
                  )}
                  {p.docs_url && p.docs_url !== p.signup_url && (
                    <a
                      href={p.docs_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-muted-foreground hover:underline"
                    >
                      {t("toolsWebSearch.docs", "文档")}
                    </a>
                  )}
                </div>

                {test && (
                  <div
                    className="wsProviderTestResult"
                    data-state={test.ok ? "ok" : "error"}
                  >
                    {test.ok ? (
                      <>
                        <strong>
                          {t("toolsWebSearch.testOk", "测试成功，返回 {{n}} 条结果", {
                            n: (test.results || []).length,
                          })}
                        </strong>
                        {(test.results || []).slice(0, 3).map((it, i) => (
                          <div key={i} className="text-xs opacity-80">
                            {i + 1}. {it.title} <span className="opacity-60">— {it.url}</span>
                          </div>
                        ))}
                      </>
                    ) : (
                      <>
                        <strong>
                          {t("toolsWebSearch.testFail", "测试失败")}
                          {test.error_code ? ` [${test.error_code}]` : ""}
                        </strong>
                        <div className="text-xs">{test.message}</div>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
