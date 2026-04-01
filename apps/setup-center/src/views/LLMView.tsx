import { useMemo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_TAURI, IS_WEB, logger, openExternalUrl } from "../platform";
import {
  isLocalProvider, localProviderPlaceholderKey, friendlyFetchError,
  fetchModelsDirectly, safeFetch,
  isMiniMaxProvider, isVolcCodingPlanProvider, isDashScopeCodingPlanProvider,
  isQianFanCodingPlanProvider, isLongCatProvider,
  miniMaxFallbackModels, volcCodingPlanFallbackModels,
  dashScopeCodingPlanFallbackModels, qianFanCodingPlanFallbackModels,
  longCatFallbackModels,
} from "../providers";
import {
  suggestEndpointName, envGet, envSet,
} from "../utils";
import { copyToClipboard } from "../utils/clipboard";
import { notifySuccess, notifyError, notifyLoading, dismissLoading } from "../utils/notify";
import { STT_RECOMMENDED_MODELS } from "../constants";
import {
  IconChevronUp, IconEdit, IconTrash, IconEye, IconEyeOff, IconPower, IconCircle,
  DotGreen, DotGray,
} from "../icons";
import { ChevronRight, XIcon, Inbox, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { SearchSelect } from "../components/SearchSelect";
import { ProviderSearchSelect } from "../components/ProviderSearchSelect";
import type { EnvMap, ProviderInfo, ListedModel, EndpointDraft } from "../types";

export interface LLMViewProps {
  savedEndpoints: EndpointDraft[];
  savedCompilerEndpoints: EndpointDraft[];
  savedSttEndpoints: EndpointDraft[];
  setSavedEndpoints: React.Dispatch<React.SetStateAction<EndpointDraft[]>>;
  setSavedCompilerEndpoints: React.Dispatch<React.SetStateAction<EndpointDraft[]>>;
  setSavedSttEndpoints: React.Dispatch<React.SetStateAction<EndpointDraft[]>>;
  envDraft: EnvMap;
  setEnvDraft: React.Dispatch<React.SetStateAction<EnvMap>>;
  secretShown: Record<string, boolean>;
  setSecretShown: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  busy: string | null;
  currentWorkspaceId: string | null;
  dataMode: "local" | "remote";
  shouldUseHttpApi: () => boolean;
  httpApiBase: () => string;
  askConfirm: (msg: string, onConfirm: () => void) => void;
  providers: ProviderInfo[];
  doLoadProviders: () => Promise<void>;
  loadSavedEndpoints: () => Promise<void>;
  readWorkspaceFile: (path: string) => Promise<string>;
  writeWorkspaceFile: (path: string, content: string) => Promise<void>;
  venvDir: string;
  ensureEnvLoaded: (wsId: string) => Promise<EnvMap>;
  serviceRunning?: boolean;
}

export function LLMView(props: LLMViewProps) {
  const {
    savedEndpoints, savedCompilerEndpoints, savedSttEndpoints,
    setSavedEndpoints, setSavedCompilerEndpoints, setSavedSttEndpoints,
    envDraft, setEnvDraft,
    secretShown, setSecretShown,
    busy, currentWorkspaceId, dataMode,
    shouldUseHttpApi, httpApiBase, askConfirm,
    providers, doLoadProviders, loadSavedEndpoints,
    readWorkspaceFile, writeWorkspaceFile,
    venvDir, ensureEnvLoaded, serviceRunning,
  } = props;

  const { t } = useTranslation();

  // Main endpoint form
  const [providerSlug, setProviderSlug] = useState<string>("");
  const selectedProvider = useMemo(
    () => providers.find((p) => p.slug === providerSlug) || null,
    [providers, providerSlug],
  );
  const [apiType, setApiType] = useState<"openai" | "openai_responses" | "anthropic">("openai");
  const [baseUrl, setBaseUrl] = useState<string>("");
  const [apiKeyValue, setApiKeyValue] = useState<string>("");
  const [models, setModels] = useState<ListedModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>("");
  const [capSelected, setCapSelected] = useState<string[]>([]);
  const [capTouched, setCapTouched] = useState(false);
  const [endpointName, setEndpointName] = useState<string>("");
  const [endpointPriority, setEndpointPriority] = useState<number>(1);
  const [endpointNameTouched, setEndpointNameTouched] = useState(false);
  const [baseUrlTouched, setBaseUrlTouched] = useState(false);
  const [baseUrlExpanded, setBaseUrlExpanded] = useState(false);
  const [editBaseUrlExpanded, setEditBaseUrlExpanded] = useState(false);
  const [compBaseUrlExpanded, setCompBaseUrlExpanded] = useState(false);
  const [sttBaseUrlExpanded, setSttBaseUrlExpanded] = useState(false);
  const [addEpMaxTokens, setAddEpMaxTokens] = useState(0);
  const [addEpContextWindow, setAddEpContextWindow] = useState(200000);
  const [addEpTimeout, setAddEpTimeout] = useState(180);
  const [addEpRpmLimit, setAddEpRpmLimit] = useState(0);
  const [codingPlanMode, setCodingPlanMode] = useState(false);

  // Compiler form
  const [compilerProviderSlug, setCompilerProviderSlug] = useState("");
  const [compilerApiType, setCompilerApiType] = useState<"openai" | "anthropic">("openai");
  const [compilerBaseUrl, setCompilerBaseUrl] = useState("");
  const [compilerApiKeyValue, setCompilerApiKeyValue] = useState("");
  const [compilerModel, setCompilerModel] = useState("");
  const [compilerEndpointName, setCompilerEndpointName] = useState("");
  const [compilerCodingPlan, setCompilerCodingPlan] = useState(false);
  const [compilerModels, setCompilerModels] = useState<ListedModel[]>([]);

  // STT form
  const [sttProviderSlug, setSttProviderSlug] = useState("");
  const [sttApiType, setSttApiType] = useState<"openai" | "anthropic">("openai");
  const [sttBaseUrl, setSttBaseUrl] = useState("");
  const [sttApiKeyValue, setSttApiKeyValue] = useState("");
  const [sttModel, setSttModel] = useState("");
  const [sttEndpointName, setSttEndpointName] = useState("");
  const [sttModels, setSttModels] = useState<ListedModel[]>([]);

  // Edit modal
  const [editingOriginalName, setEditingOriginalName] = useState<string | null>(null);
  const [editEndpointType, setEditEndpointType] = useState<"endpoints" | "compiler_endpoints" | "stt_endpoints">("endpoints");
  const [editModalOpen, setEditModalOpen] = useState(false);
  const isEditingEndpoint = editModalOpen && editingOriginalName !== null;
  const [editDraft, setEditDraft] = useState<{
    name: string; priority: number; providerSlug: string;
    apiType: "openai" | "openai_responses" | "anthropic";
    baseUrl: string; apiKeyEnv: string; apiKeyValue: string;
    modelId: string; caps: string[]; maxTokens: number;
    contextWindow: number; timeout: number; rpmLimit: number;
    pricingTiers: { max_input: number; input_price: number; output_price: number }[];
  } | null>(null);
  const [editModels, setEditModels] = useState<ListedModel[]>([]);

  // Dialog open states
  const [addEpDialogOpen, setAddEpDialogOpen] = useState(false);
  const [addCompDialogOpen, setAddCompDialogOpen] = useState(false);
  const [addSttDialogOpen, setAddSttDialogOpen] = useState(false);

  // Connection test
  const [connTesting, setConnTesting] = useState(false);
  const [connTestResult, setConnTestResult] = useState<{
    ok: boolean; latencyMs: number; error?: string; modelCount?: number;
  } | null>(null);

  const propsRef = useRef(props);
  propsRef.current = props;

  // ── Utility constants & helpers ──

  const PROVIDER_APPLY_URLS: Record<string, string> = {
    openai: "https://platform.openai.com/api-keys",
    anthropic: "https://console.anthropic.com/settings/keys",
    moonshot: "https://platform.moonshot.cn/console",
    kimi: "https://platform.moonshot.cn/console",
    "kimi-cn": "https://platform.moonshot.cn/console",
    "kimi-int": "https://platform.moonshot.ai/console/api-keys",
    dashscope: "https://dashscope.console.aliyun.com/",
    minimax: "https://platform.minimaxi.com/user-center/basic-information/interface-key",
    "minimax-cn": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
    "minimax-int": "https://platform.minimax.io/user-center/basic-information/interface-key",
    deepseek: "https://platform.deepseek.com/",
    openrouter: "https://openrouter.ai/",
    siliconflow: "https://siliconflow.cn/",
    volcengine: "https://console.volcengine.com/ark/",
    zhipu: "https://open.bigmodel.cn/",
    "zhipu-cn": "https://open.bigmodel.cn/usercenter/apikeys",
    "zhipu-int": "https://z.ai/manage-apikey/apikey-list",
    yunwu: "https://yunwu.zeabur.app/",
    ollama: "https://ollama.com/library",
    lmstudio: "https://lmstudio.ai/",
  };

  function getProviderApplyUrl(slug: string): string {
    return PROVIDER_APPLY_URLS[slug.toLowerCase()] || "";
  }

  async function openApplyUrl(url: string) {
    try { await openExternalUrl(url); } catch {
      const ok = await copyToClipboard(url);
      if (ok) notifySuccess("链接已复制到剪贴板：" + url);
      else notifyError("无法打开链接，请手动访问：" + url);
    }
  }

  function normalizePriority(n: any, fallback: number) {
    const x = Number(n);
    if (!Number.isFinite(x) || x <= 0) return fallback;
    return Math.floor(x);
  }

  const providerApplyUrl = useMemo(() => getProviderApplyUrl(selectedProvider?.slug || ""), [selectedProvider?.slug]);

  // ── Effects ──

  useEffect(() => {
    loadSavedEndpoints().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId, dataMode, serviceRunning]);

  useEffect(() => {
    if (!selectedProvider) return;
    if (codingPlanMode && selectedProvider.coding_plan_base_url) {
      setApiType((selectedProvider.coding_plan_api_type as "openai" | "anthropic") || "anthropic");
      if (!baseUrlTouched) setBaseUrl(selectedProvider.coding_plan_base_url);
      setAddEpContextWindow(200000);
      setAddEpMaxTokens((selectedProvider as ProviderInfo).default_max_tokens ?? 8192);
    } else {
      const at = (selectedProvider.api_type as "openai" | "anthropic") || "openai";
      setApiType(at);
      if (!baseUrlTouched) setBaseUrl(selectedProvider.default_base_url || "");
      setAddEpContextWindow((selectedProvider as ProviderInfo).default_context_window ?? 200000);
      setAddEpMaxTokens((selectedProvider as ProviderInfo).default_max_tokens ?? 0);
    }
    const autoName = suggestEndpointName(selectedProvider.slug, selectedModelId);
    if (!endpointNameTouched) {
      setEndpointName(autoName);
    }
    if (isLocalProvider(selectedProvider) && !apiKeyValue.trim()) {
      setApiKeyValue(localProviderPlaceholderKey(selectedProvider));
    }
  }, [selectedProvider, selectedModelId, endpointNameTouched, baseUrlTouched, codingPlanMode]);

  useEffect(() => {
    if (!providerSlug) return;
    if (editModalOpen) return;
    setEndpointNameTouched(false);
    setBaseUrlTouched(false);
    setCodingPlanMode(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerSlug]);

  useEffect(() => {
    if (!selectedProvider) return;
    const effectiveBaseUrl = (codingPlanMode ? selectedProvider.coding_plan_base_url : selectedProvider.default_base_url) || "";
    if (isVolcCodingPlanProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(volcCodingPlanFallbackModels(selectedProvider.slug));
      return;
    }
    if (isDashScopeCodingPlanProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(dashScopeCodingPlanFallbackModels(selectedProvider.slug));
      return;
    }
    if (isQianFanCodingPlanProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(qianFanCodingPlanFallbackModels(selectedProvider.slug));
      return;
    }
    if (isLongCatProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(longCatFallbackModels(selectedProvider.slug));
      return;
    }
    if (isMiniMaxProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(miniMaxFallbackModels(selectedProvider.slug));
      return;
    }
  }, [selectedProvider, codingPlanMode]);

  useEffect(() => {
    if (capTouched) return;
    const caps = models.find((m) => m.id === selectedModelId)?.capabilities ?? {};
    const list = Object.entries(caps)
      .filter(([, v]) => v)
      .map(([k]) => k);
    setCapSelected(list.length ? list : ["text"]);
  }, [selectedModelId, models, capTouched]);

  useEffect(() => {
    if (isEditingEndpoint) return;
    const maxP = savedEndpoints.reduce((m, e) => Math.max(m, Number.isFinite(e.priority) ? e.priority : 0), 0);
    setEndpointPriority(savedEndpoints.length === 0 ? 1 : maxP + 1);
  }, [savedEndpoints, isEditingEndpoint]);

  // ── Async functions ──

  async function fetchModelListUnified(params: {
    apiType: string; baseUrl: string; providerSlug: string | null; apiKey: string;
  }): Promise<ListedModel[]> {
    logger.debug("LLMView", "fetchModelListUnified", { shouldUseHttpApi: shouldUseHttpApi(), httpApiBase: httpApiBase() });
    if (shouldUseHttpApi()) {
      logger.debug("LLMView", "fetchModelListUnified: using HTTP API");
      try {
        const res = await safeFetch(`${httpApiBase()}/api/config/list-models`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            api_type: params.apiType,
            base_url: params.baseUrl,
            provider_slug: params.providerSlug || null,
            api_key: params.apiKey,
          }),
          signal: AbortSignal.timeout(30_000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        return Array.isArray(data.models) ? data.models : data;
      } catch (httpErr) {
        const msg = String(httpErr);
        if (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("AbortError")) {
          logger.warn("LLMView", "fetchModelListUnified: HTTP API unreachable, falling back", { error: String(httpErr) });
        } else {
          throw httpErr;
        }
      }
    }
    try {
      const raw = await invoke<string>("openakita_list_models", {
        venvDir,
        apiType: params.apiType,
        baseUrl: params.baseUrl,
        providerSlug: params.providerSlug,
        apiKey: params.apiKey,
      });
      return JSON.parse(raw) as ListedModel[];
    } catch (e) {
      logger.warn("LLMView", "openakita_list_models via Python bridge failed, using direct fetch", { error: String(e) });
    }
    return fetchModelsDirectly(params);
  }

  async function doFetchModels() {
    setModels([]);
    setSelectedModelId("");
    const _busyId = notifyLoading(t("llm.fetchingModels"));
    try {
      const effectiveKey = apiKeyValue.trim() || (isLocalProvider(selectedProvider) ? localProviderPlaceholderKey(selectedProvider) : "");
      logger.debug("LLMView", "doFetchModels", { apiType, baseUrl, slug: selectedProvider?.slug, keyLen: effectiveKey?.length, httpApi: shouldUseHttpApi(), isLocal: isLocalProvider(selectedProvider) });
      const parsed = await fetchModelListUnified({
        apiType,
        baseUrl,
        providerSlug: selectedProvider?.slug ?? null,
        apiKey: effectiveKey,
      });
      setModels(parsed);
      setSelectedModelId("");
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
      setCapTouched(false);
    } catch (e: any) {
      logger.error("LLMView", "doFetchModels error", { error: String(e) });
      const raw = String(e?.message || e);
      notifyError(friendlyFetchError(raw, t, selectedProvider?.name));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doTestConnection(params: {
    testApiType: string; testBaseUrl: string; testApiKey: string; testProviderSlug?: string | null;
  }) {
    setConnTesting(true);
    setConnTestResult(null);
    const t0 = performance.now();
    try {
      let modelCount = 0;
      let httpApiFailed = false;
      if (shouldUseHttpApi()) {
        try {
          const base = httpApiBase();
          const res = await safeFetch(`${base}/api/config/list-models`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              api_type: params.testApiType,
              base_url: params.testBaseUrl,
              provider_slug: params.testProviderSlug || null,
              api_key: params.testApiKey,
            }),
            signal: AbortSignal.timeout(30_000),
          });
          const data = await res.json();
          if (data.error) throw new Error(data.error);
          const fetchedModels = Array.isArray(data.models) ? data.models : (Array.isArray(data) ? data : []);
          modelCount = fetchedModels.length;
        } catch (httpErr) {
          const msg = String(httpErr);
          if (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("AbortError")) {
            logger.warn("LLMView", "doTestConnection: HTTP API unreachable, falling back to direct", { error: String(httpErr) });
            httpApiFailed = true;
          } else {
            throw httpErr;
          }
        }
      }
      if (!shouldUseHttpApi() || httpApiFailed) {
        const result = await fetchModelsDirectly({
          apiType: params.testApiType,
          baseUrl: params.testBaseUrl,
          providerSlug: params.testProviderSlug ?? null,
          apiKey: params.testApiKey,
        });
        modelCount = result.length;
      }
      const latency = Math.round(performance.now() - t0);
      setConnTestResult({ ok: true, latencyMs: latency, modelCount });
    } catch (e) {
      const latency = Math.round(performance.now() - t0);
      const raw = String(e);
      const provName = providers.find((p) => p.slug === params.testProviderSlug)?.name;
      const errMsg = friendlyFetchError(raw, t, provName);
      setConnTestResult({ ok: false, latencyMs: latency, error: errMsg });
    } finally {
      setConnTesting(false);
    }
  }

  async function doFetchCompilerModels() {
    const compilerSelectedProvider = providers.find((p) => p.slug === compilerProviderSlug) || null;
    const isCompilerLocal = isLocalProvider(compilerSelectedProvider);
    if (!compilerApiKeyValue.trim() && !isCompilerLocal) {
      notifyError("请先填写编译端点的 API Key 值");
      return;
    }
    if (!compilerBaseUrl.trim()) {
      notifyError("请先填写编译端点的 Base URL");
      return;
    }
    setCompilerModels([]);
    const _busyId = notifyLoading("拉取编译端点模型列表...");
    try {
      const effectiveCompilerKey = compilerApiKeyValue.trim() || (isCompilerLocal ? localProviderPlaceholderKey(compilerSelectedProvider) : "");
      const parsed = await fetchModelListUnified({
        apiType: compilerApiType,
        baseUrl: compilerBaseUrl,
        providerSlug: compilerProviderSlug || null,
        apiKey: effectiveCompilerKey,
      });
      setCompilerModels(parsed);
      setCompilerModel("");
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
    } catch (e: any) {
      const raw = String(e?.message || e);
      const cprov = providers.find((p) => p.slug === compilerProviderSlug);
      notifyError(friendlyFetchError(raw, t, cprov?.name));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doFetchSttModels() {
    const sttSelectedProvider = providers.find((p) => p.slug === sttProviderSlug) || null;
    const isSttLocal = isLocalProvider(sttSelectedProvider);
    if (!sttApiKeyValue.trim() && !isSttLocal) {
      notifyError("请先填写 STT 端点的 API Key 值");
      return;
    }
    if (!sttBaseUrl.trim()) {
      notifyError("请先填写 STT 端点的 Base URL");
      return;
    }
    setSttModels([]);
    const _busyId = notifyLoading("拉取 STT 端点模型列表...");
    try {
      const effectiveKey = sttApiKeyValue.trim() || (isSttLocal ? localProviderPlaceholderKey(sttSelectedProvider) : "");
      const parsed = await fetchModelListUnified({
        apiType: sttApiType,
        baseUrl: sttBaseUrl,
        providerSlug: sttProviderSlug || null,
        apiKey: effectiveKey,
      });
      setSttModels(parsed);
      setSttModel("");
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
    } catch (e: any) {
      const raw = String(e?.message || e);
      const sprov = providers.find((p) => p.slug === sttProviderSlug);
      notifyError(friendlyFetchError(raw, t, sprov?.name));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveCompilerEndpoint(): Promise<boolean> {
    if (!currentWorkspaceId && dataMode !== "remote") {
      notifyError("请先创建/选择一个当前工作区");
      return false;
    }
    if (!compilerModel.trim()) {
      notifyError("请填写编译模型名称");
      return false;
    }
    if (!compilerBaseUrl.trim()) {
      notifyError("请填写编译端点的 Base URL");
      return false;
    }
    if (!/^https?:\/\//i.test(compilerBaseUrl.trim())) {
      notifyError("编译端点 Base URL 必须以 http:// 或 https:// 开头");
      return false;
    }
    const compilerSelectedProvider = providers.find((p) => p.slug === compilerProviderSlug) || null;
    const isCompilerLocal = isLocalProvider(compilerSelectedProvider);
    const effectiveCompApiKeyValue = compilerApiKeyValue.trim() || (isCompilerLocal ? localProviderPlaceholderKey(compilerSelectedProvider) : "");
    if (!isCompilerLocal && !effectiveCompApiKeyValue) {
      notifyError("请填写编译端点的 API Key 值");
      return false;
    }
    const _busyId = notifyLoading("写入编译端点...");
    try {
      const epName = (compilerEndpointName.trim() || `compiler-${compilerProviderSlug || "provider"}-${compilerModel.trim()}`).slice(0, 64);

      const endpoint: Record<string, unknown> = {
        name: epName,
        provider: compilerProviderSlug || "custom",
        api_type: compilerApiType,
        base_url: compilerBaseUrl.trim(),
        model: compilerModel.trim(),
        max_tokens: 2048,
        context_window: 200000,
        timeout: 30,
        capabilities: ["text"],
      };

      const res = await safeFetch(`${httpApiBase()}/api/config/save-endpoint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint,
          api_key: effectiveCompApiKeyValue || null,
          endpoint_type: "compiler_endpoints",
        }),
      });
      const data = await res.json();
      if (data.status === "error" || data.status === "conflict") {
        notifyError(data.error || "保存失败");
        return false;
      }
      if (effectiveCompApiKeyValue && data.endpoint?.api_key_env) {
        setEnvDraft((e) => envSet(e, data.endpoint.api_key_env, effectiveCompApiKeyValue));
      }

      setCompilerModel("");
      setCompilerApiKeyValue("");
      setCompilerEndpointName("");
      setCompilerBaseUrl("");
      notifySuccess(`编译端点 ${epName} 已保存`);
      await loadSavedEndpoints();
      return true;
    } catch (e) {
      notifyError(String(e));
      return false;
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doDeleteCompilerEndpoint(epName: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    const _busyId = notifyLoading("删除编译端点...");
    try {
      await safeFetch(
        `${httpApiBase()}/api/config/endpoint/${encodeURIComponent(epName)}?endpoint_type=compiler_endpoints`,
        { method: "DELETE" },
      );
      setSavedCompilerEndpoints((prev) => prev.filter((e) => e.name !== epName));
      notifySuccess(`编译端点 ${epName} 已删除`);
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveSttEndpoint(): Promise<boolean> {
    if (!currentWorkspaceId && dataMode !== "remote") {
      notifyError("请先创建/选择一个当前工作区");
      return false;
    }
    if (!sttModel.trim()) {
      notifyError("请填写 STT 模型名称");
      return false;
    }
    if (!sttBaseUrl.trim()) {
      notifyError("请填写 STT 端点的 Base URL");
      return false;
    }
    if (!/^https?:\/\//i.test(sttBaseUrl.trim())) {
      notifyError("STT 端点 Base URL 必须以 http:// 或 https:// 开头");
      return false;
    }
    const sttSelectedProvider = providers.find((p) => p.slug === sttProviderSlug) || null;
    const isSttLocal = isLocalProvider(sttSelectedProvider);
    const effectiveSttApiKeyValue = sttApiKeyValue.trim() || (isSttLocal ? localProviderPlaceholderKey(sttSelectedProvider) : "");
    if (!isSttLocal && !effectiveSttApiKeyValue) {
      notifyError("请填写 STT 端点的 API Key 值");
      return false;
    }
    const _busyId = notifyLoading("保存 STT 端点...");
    try {
      const epName = (sttEndpointName.trim() || `stt-${sttProviderSlug || "provider"}-${sttModel.trim()}`).slice(0, 64);

      const endpoint: Record<string, unknown> = {
        name: epName,
        provider: sttProviderSlug || "custom",
        api_type: sttApiType,
        base_url: sttBaseUrl.trim(),
        model: sttModel.trim(),
        max_tokens: 0,
        context_window: 0,
        timeout: 60,
        capabilities: ["text"],
      };

      const res = await safeFetch(`${httpApiBase()}/api/config/save-endpoint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint,
          api_key: effectiveSttApiKeyValue || null,
          endpoint_type: "stt_endpoints",
        }),
      });
      const data = await res.json();
      if (data.status === "error" || data.status === "conflict") {
        notifyError(data.error || "保存失败");
        return false;
      }
      if (effectiveSttApiKeyValue && data.endpoint?.api_key_env) {
        setEnvDraft((e) => envSet(e, data.endpoint.api_key_env, effectiveSttApiKeyValue));
      }

      setSttModel("");
      setSttApiKeyValue("");
      setSttEndpointName("");
      setSttBaseUrl("");
      setSttModels([]);
      notifySuccess(`STT 端点 ${epName} 已保存`);
      await loadSavedEndpoints();
      return true;
    } catch (e) {
      notifyError(String(e));
      return false;
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doDeleteSttEndpoint(epName: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    const _busyId = notifyLoading("删除 STT 端点...");
    try {
      await safeFetch(
        `${httpApiBase()}/api/config/endpoint/${encodeURIComponent(epName)}?endpoint_type=stt_endpoints`,
        { method: "DELETE" },
      );
      setSavedSttEndpoints((prev) => prev.filter((e) => e.name !== epName));
      notifySuccess(`STT 端点 ${epName} 已删除`);
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doReorderByNames(orderedNames: string[]) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    const _busyId = notifyLoading("保存排序...");
    try {
      const res = await safeFetch(`${httpApiBase()}/api/config/reorder-endpoints`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ordered_names: orderedNames, endpoint_type: "endpoints" }),
      });
      const json = await res.json();
      if (json.status !== "ok") throw new Error(json.error || "reorder failed");
      notifySuccess("已保存端点顺序（priority 已更新）");
      await loadSavedEndpoints();
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSetPrimaryEndpoint(name: string) {
    const names = savedEndpoints.map((e) => e.name);
    const idx = names.indexOf(name);
    if (idx < 0) return;
    const next = [name, ...names.filter((n) => n !== name)];
    await doReorderByNames(next);
  }

  async function doStartEditEndpoint(name: string) {
    const ep = savedEndpoints.find((e) => e.name === name);
    if (!ep) return;
    if (currentWorkspaceId) {
      await ensureEnvLoaded(currentWorkspaceId);
    } else if (dataMode === "remote") {
      await ensureEnvLoaded("__remote__");
    }
    setEditEndpointType("endpoints");
    setEditingOriginalName(name);
    setEditDraft({
      name: ep.name,
      priority: normalizePriority(ep.priority, 1),
      providerSlug: ep.provider || "",
      apiType: (ep.api_type as any) || "openai",
      baseUrl: ep.base_url || "",
      apiKeyEnv: ep.api_key_env || "",
      apiKeyValue: envDraft[ep.api_key_env || ""] || "",
      modelId: ep.model || "",
      caps: Array.isArray(ep.capabilities) && ep.capabilities.length ? ep.capabilities : ["text"],
      maxTokens: typeof ep.max_tokens === "number" ? ep.max_tokens : 0,
      contextWindow: typeof ep.context_window === "number" ? ep.context_window : 200000,
      timeout: typeof ep.timeout === "number" ? ep.timeout : 180,
      rpmLimit: typeof ep.rpm_limit === "number" ? ep.rpm_limit : 0,
      pricingTiers: Array.isArray(ep.pricing_tiers) ? ep.pricing_tiers.map((tier: any) => ({
        max_input: Number.isFinite(Number(tier?.max_input)) ? Number(tier.max_input) : 0,
        input_price: Number.isFinite(Number(tier?.input_price)) ? Number(tier.input_price) : 0,
        output_price: Number.isFinite(Number(tier?.output_price)) ? Number(tier.output_price) : 0,
      })) : [],
    });
    setEditModalOpen(true);
    setConnTestResult(null);
  }

  async function doStartEditCompilerEndpoint(name: string) {
    const ep = savedCompilerEndpoints.find((e) => e.name === name);
    if (!ep) return;
    if (currentWorkspaceId) {
      await ensureEnvLoaded(currentWorkspaceId);
    } else if (dataMode === "remote") {
      await ensureEnvLoaded("__remote__");
    }
    setEditEndpointType("compiler_endpoints");
    setEditingOriginalName(name);
    setEditDraft({
      name: ep.name,
      priority: 1,
      providerSlug: ep.provider || "",
      apiType: (ep.api_type as any) || "openai",
      baseUrl: ep.base_url || "",
      apiKeyEnv: ep.api_key_env || "",
      apiKeyValue: envDraft[ep.api_key_env || ""] || "",
      modelId: ep.model || "",
      caps: ["text"],
      maxTokens: typeof ep.max_tokens === "number" ? ep.max_tokens : 2048,
      contextWindow: typeof ep.context_window === "number" ? ep.context_window : 200000,
      timeout: typeof ep.timeout === "number" ? ep.timeout : 30,
      rpmLimit: 0,
      pricingTiers: [],
    });
    setEditModalOpen(true);
    setConnTestResult(null);
  }

  async function doStartEditSttEndpoint(name: string) {
    const ep = savedSttEndpoints.find((e) => e.name === name);
    if (!ep) return;
    if (currentWorkspaceId) {
      await ensureEnvLoaded(currentWorkspaceId);
    } else if (dataMode === "remote") {
      await ensureEnvLoaded("__remote__");
    }
    setEditEndpointType("stt_endpoints");
    setEditingOriginalName(name);
    setEditDraft({
      name: ep.name,
      priority: 1,
      providerSlug: ep.provider || "",
      apiType: (ep.api_type as any) || "openai",
      baseUrl: ep.base_url || "",
      apiKeyEnv: ep.api_key_env || "",
      apiKeyValue: envDraft[ep.api_key_env || ""] || "",
      modelId: ep.model || "",
      caps: ["text"],
      maxTokens: 0,
      contextWindow: 0,
      timeout: typeof ep.timeout === "number" ? ep.timeout : 60,
      rpmLimit: 0,
      pricingTiers: [],
    });
    setEditModalOpen(true);
    setConnTestResult(null);
  }

  function resetEndpointEditor() {
    setEditingOriginalName(null);
    setEditEndpointType("endpoints");
    setEditDraft(null);
    setEditModalOpen(false);
    setEditModels([]);
    setSecretShown((m) => ({ ...m, __EDIT_EP_KEY: false }));
    setCodingPlanMode(false);
  }

  async function doFetchEditModels() {
    if (!editDraft) return;
    const editProvider = providers.find((p) => p.slug === editDraft.providerSlug);
    const isEditLocal = isLocalProvider(editProvider);
    const key = editDraft.apiKeyValue.trim() || envGet(envDraft, editDraft.apiKeyEnv) || (isEditLocal ? localProviderPlaceholderKey(editProvider) : "");
    if (!isEditLocal && !key) {
      notifyError("请先填写 API Key 值（或确保对应环境变量已有值）");
      return;
    }
    if (!editDraft.baseUrl.trim()) {
      notifyError("请先填写 Base URL");
      return;
    }
    const _busyId = notifyLoading(t("llm.fetchingModels"));
    try {
      const parsed = await fetchModelListUnified({
        apiType: editDraft.apiType,
        baseUrl: editDraft.baseUrl,
        providerSlug: editDraft.providerSlug || null,
        apiKey: key || "local",
      });
      setEditModels(parsed);
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
    } catch (e: any) {
      const raw = String(e?.message || e);
      const eprov = providers.find((p) => p.slug === (editDraft?.providerSlug || ""));
      notifyError(friendlyFetchError(raw, t, eprov?.name));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveEditedEndpoint() {
    if (!currentWorkspaceId && dataMode !== "remote") {
      notifyError("请先创建/选择一个当前工作区");
      return;
    }
    if (!editDraft || !editingOriginalName) return;
    if (!editDraft.name.trim()) {
      notifyError("端点名称不能为空");
      return;
    }
    if (!editDraft.modelId.trim()) {
      notifyError("模型不能为空");
      return;
    }
    if (!editDraft.baseUrl.trim()) {
      notifyError("请填写 Base URL");
      return;
    }
    if (!/^https?:\/\//i.test(editDraft.baseUrl.trim())) {
      notifyError("Base URL 必须以 http:// 或 https:// 开头");
      return;
    }
    const _busyId = notifyLoading("保存修改...");
    const epType = editEndpointType;
    try {
      const newName = editDraft.name.trim().slice(0, 64);
      const nameChanged = newName !== editingOriginalName;

      const endpoint: Record<string, unknown> = {
        name: nameChanged ? newName : editingOriginalName,
        provider: editDraft.providerSlug || "custom",
        api_type: editDraft.apiType,
        base_url: editDraft.baseUrl.trim(),
        api_key_env: editDraft.apiKeyEnv || undefined,
        model: editDraft.modelId.trim(),
        capabilities: ["text"],
      };

      if (epType === "endpoints") {
        const validTiers = (editDraft.pricingTiers || []).filter(
          (tier) => tier.input_price > 0 || tier.output_price > 0
        );
        endpoint.priority = normalizePriority(editDraft.priority, 1);
        endpoint.max_tokens = editDraft.maxTokens ?? 0;
        endpoint.context_window = editDraft.contextWindow ?? 200000;
        endpoint.timeout = editDraft.timeout ?? 180;
        endpoint.rpm_limit = editDraft.rpmLimit ?? 0;
        endpoint.capabilities = editDraft.caps?.length ? editDraft.caps : ["text"];
        if ((editDraft.caps || []).includes("thinking") && editDraft.providerSlug === "dashscope") {
          endpoint.extra_params = { enable_thinking: true };
        }
        if (validTiers.length > 0) {
          endpoint.pricing_tiers = validTiers;
        }
      } else if (epType === "compiler_endpoints") {
        endpoint.max_tokens = editDraft.maxTokens ?? 2048;
        endpoint.context_window = editDraft.contextWindow ?? 200000;
        endpoint.timeout = editDraft.timeout ?? 30;
      } else {
        endpoint.max_tokens = 0;
        endpoint.context_window = 0;
        endpoint.timeout = editDraft.timeout ?? 60;
      }

      if (nameChanged) {
        await safeFetch(
          `${httpApiBase()}/api/config/endpoint/${encodeURIComponent(editingOriginalName)}?endpoint_type=${epType}`,
          { method: "DELETE" },
        );
      }
      const keyToSave = editDraft.apiKeyValue.trim() || null;
      const res = await safeFetch(`${httpApiBase()}/api/config/save-endpoint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint,
          api_key: keyToSave,
          endpoint_type: epType,
        }),
      });
      const data = await res.json();
      if (data.status === "conflict" || data.status === "error") {
        notifyError(data.error || "保存失败");
        return;
      }
      if (keyToSave && data.endpoint?.api_key_env) {
        setEnvDraft((e) => envSet(e, data.endpoint.api_key_env, keyToSave));
      }

      notifySuccess("端点已更新");
      setEditModalOpen(false);
      await loadSavedEndpoints();
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveEndpoint(): Promise<boolean> {
    if (!currentWorkspaceId && dataMode !== "remote") {
      notifyError("请先创建/选择一个当前工作区");
      return false;
    }
    if (!selectedModelId) {
      notifyError("请先选择模型");
      return false;
    }
    if (!baseUrl.trim()) {
      notifyError("请填写 Base URL");
      return false;
    }
    if (!/^https?:\/\//i.test(baseUrl.trim())) {
      notifyError("Base URL 必须以 http:// 或 https:// 开头");
      return false;
    }
    const isLocal = isLocalProvider(selectedProvider);
    const effectiveApiKeyValue = apiKeyValue.trim() || (isLocal ? localProviderPlaceholderKey(selectedProvider) : "");
    if (!isLocal && !effectiveApiKeyValue) {
      notifyError("请填写 API Key 值（会写入工作区 .env）");
      return false;
    }
    const _busyId = notifyLoading(isEditingEndpoint ? t("llm.updatingEndpoint") : t("llm.savingEndpoint"));

    try {
      const capList = Array.isArray(capSelected) && capSelected.length ? capSelected : ["text"];
      const epName = (endpointName.trim() || `${providerSlug || selectedProvider?.slug || "provider"}-${selectedModelId}`).slice(0, 64);

      const endpoint: Record<string, unknown> = {
        name: isEditingEndpoint ? (editingOriginalName || epName) : epName,
        provider: providerSlug || (selectedProvider?.slug ?? "custom"),
        api_type: apiType,
        base_url: baseUrl.trim(),
        model: selectedModelId,
        priority: normalizePriority(endpointPriority, 1),
        max_tokens: addEpMaxTokens,
        context_window: addEpContextWindow,
        timeout: addEpTimeout,
        rpm_limit: addEpRpmLimit,
        capabilities: capList,
      };
      if (capList.includes("thinking") && (providerSlug || selectedProvider?.slug) === "dashscope") {
        endpoint.extra_params = { enable_thinking: true };
      }

      const res = await safeFetch(`${httpApiBase()}/api/config/save-endpoint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint,
          api_key: effectiveApiKeyValue || null,
          endpoint_type: "endpoints",
        }),
      });
      const data = await res.json();
      if (data.status === "conflict") {
        notifyError(data.error || t("llm.configConflict"));
        return false;
      }
      if (data.status === "error") {
        notifyError(data.error || "保存失败");
        return false;
      }
      if (effectiveApiKeyValue && data.endpoint?.api_key_env) {
        setEnvDraft((e) => envSet(e, data.endpoint.api_key_env, effectiveApiKeyValue));
      }

      notifySuccess(
        isEditingEndpoint
          ? "端点已更新（同时已写入 API Key 到 .env）。"
          : "端点已保存（同时已写入 API Key 到 .env）。你可以继续添加备份端点。",
      );
      if (isEditingEndpoint) resetEndpointEditor();
      await loadSavedEndpoints();
      return true;
    } catch (e) {
      notifyError(String(e));
      return false;
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doDeleteEndpoint(name: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    const _busyId = notifyLoading("删除端点...");
    try {
      await safeFetch(
        `${httpApiBase()}/api/config/endpoint/${encodeURIComponent(name)}?endpoint_type=endpoints`,
        { method: "DELETE" },
      );
      setSavedEndpoints((prev) => prev.filter((e) => e.name !== name));
      notifySuccess(`已删除端点：${name}`);
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doToggleEndpointEnabled(name: string, endpointType: "endpoints" | "compiler_endpoints" | "stt_endpoints" = "endpoints") {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    try {
      const res = await safeFetch(`${httpApiBase()}/api/config/toggle-endpoint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, endpoint_type: endpointType }),
      });
      const json = await res.json();
      if (json.status !== "ok") throw new Error(json.error || "toggle failed");
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    }
  }

  function openAddEpDialog() {
    resetEndpointEditor();
    setConnTestResult(null);
    setProviderSlug(providers.find(p => p.slug === "openai")?.slug ?? providers[0]?.slug ?? "");
    setApiType("openai");
    setBaseUrl("");
    setBaseUrlTouched(false);
    setApiKeyValue("");
    setModels([]);
    setSelectedModelId("");
    setEndpointName("");
    setEndpointNameTouched(false);
    setCapSelected([]);
    setCapTouched(false);
    setEndpointPriority(1);
    setCodingPlanMode(false);
    setAddEpMaxTokens(0);
    setAddEpContextWindow(200000);
    setAddEpTimeout(180);
    setAddEpRpmLimit(0);
    if (providers.length === 0) doLoadProviders();
    setAddEpDialogOpen(true);
  }

  return (
    <>
      {/* ── Main endpoint list ── */}
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div>
            <div className="cardTitle" style={{ marginBottom: 2 }}>{t("llm.title")}</div>
            <div className="cardHint">{t("llm.subtitle")}</div>
          </div>
          <Button size="sm" onClick={openAddEpDialog} disabled={!!busy}>
            + {t("llm.addEndpoint")}
          </Button>
        </div>

        {savedEndpoints.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
            <Inbox size={32} strokeWidth={1.5} className="mb-2 opacity-40" />
            <p className="text-sm">{t("llm.noEndpoints")}</p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{t("status.endpoint")}</TableHead>
                <TableHead>{t("status.model")}</TableHead>
                <TableHead className="w-[50px]">Key</TableHead>
                <TableHead className="w-[80px]">Priority</TableHead>
                <TableHead className="w-[140px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {savedEndpoints.map((e) => (
                <TableRow key={e.name} className={e.enabled === false ? "opacity-45" : undefined}>
                  <TableCell className="font-semibold">
                    {e.name}
                    {savedEndpoints[0]?.name === e.name && e.enabled !== false && <span className="ml-1.5 text-[10px] font-extrabold text-primary">{t("llm.primary")}</span>}
                    {e.enabled === false && <span className="ml-1.5 text-[10px] font-bold text-muted-foreground">{t("llm.disabled")}</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{e.model}</TableCell>
                  <TableCell>{(envDraft[e.api_key_env] || "").trim() ? <DotGreen /> : <DotGray />}</TableCell>
                  <TableCell>{e.priority}</TableCell>
                  <TableCell>
                    <div className="flex gap-1 justify-end">
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" style={savedEndpoints[0]?.name === e.name ? { visibility: "hidden" } : undefined} onClick={() => doSetPrimaryEndpoint(e.name)} disabled={!!busy} title={t("llm.setPrimary")}><IconChevronUp size={14} /></Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doToggleEndpointEnabled(e.name)} disabled={!!busy} title={e.enabled === false ? t("llm.enable") : t("llm.disable")}>{e.enabled !== false ? <IconPower size={14} /> : <IconCircle size={14} />}</Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doStartEditEndpoint(e.name)} disabled={!!busy} title={t("llm.edit")}><IconEdit size={14} /></Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* ── Compiler endpoints ── */}
      <div className="card" style={{ marginTop: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div>
            <div className="cardTitle" style={{ marginBottom: 2 }}>{t("llm.compiler")}</div>
            <div className="cardHint">{t("llm.compilerHint")}</div>
          </div>
          <Button variant="outline" size="sm" className="bg-primary/5 border-primary/30 text-primary hover:bg-primary/10 hover:text-primary" onClick={() => { if (providers.length === 0) doLoadProviders(); setCompilerProviderSlug(""); setCompilerApiType("openai"); setCompilerBaseUrl(""); setCompilerApiKeyValue(""); setCompilerModel(""); setCompilerEndpointName(""); setCompilerCodingPlan(false); setCompilerModels([]); setAddCompDialogOpen(true); }} disabled={!!busy}>
            + {t("llm.addEndpoint")}
          </Button>
        </div>
        {savedCompilerEndpoints.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
            <Inbox size={32} strokeWidth={1.5} className="mb-2 opacity-40" />
            <p className="text-sm">{t("llm.noEndpoints")}</p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{t("status.endpoint")}</TableHead>
                <TableHead>{t("status.model")}</TableHead>
                <TableHead className="w-[110px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {savedCompilerEndpoints.map((e) => (
                <TableRow key={e.name} className={e.enabled === false ? "opacity-45" : undefined}>
                  <TableCell className="font-semibold">
                    {e.name}
                    {e.enabled === false && <span className="ml-1.5 text-[10px] font-bold text-muted-foreground">{t("llm.disabled")}</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{e.model}</TableCell>
                  <TableCell>
                    <div className="flex gap-1 justify-end">
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doToggleEndpointEnabled(e.name, "compiler_endpoints")} disabled={!!busy} title={e.enabled === false ? t("llm.enable") : t("llm.disable")}>{e.enabled !== false ? <IconPower size={14} /> : <IconCircle size={14} />}</Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doStartEditCompilerEndpoint(e.name)} disabled={!!busy} title={t("llm.edit")}><IconEdit size={14} /></Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteCompilerEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* ── STT endpoints ── */}
      <div className="card" style={{ marginTop: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div>
            <div className="cardTitle" style={{ marginBottom: 2 }}>{t("llm.stt")}</div>
            <div className="cardHint">{t("llm.sttHint")}</div>
          </div>
          <Button variant="outline" size="sm" className="bg-primary/5 border-primary/30 text-primary hover:bg-primary/10 hover:text-primary" onClick={() => { if (providers.length === 0) doLoadProviders(); setSttProviderSlug(""); setSttApiType("openai"); setSttBaseUrl(""); setSttApiKeyValue(""); setSttModel(""); setSttEndpointName(""); setSttModels([]); setAddSttDialogOpen(true); }} disabled={!!busy}>
            + {t("llm.addEndpoint")}
          </Button>
        </div>
        {savedSttEndpoints.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
            <Inbox size={32} strokeWidth={1.5} className="mb-2 opacity-40" />
            <p className="text-sm">{t("llm.noEndpoints")}</p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{t("status.endpoint")}</TableHead>
                <TableHead>{t("status.model")}</TableHead>
                <TableHead className="w-[110px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {savedSttEndpoints.map((e) => (
                <TableRow key={e.name} className={e.enabled === false ? "opacity-45" : undefined}>
                  <TableCell className="font-semibold">
                    {e.name}
                    {e.enabled === false && <span className="ml-1.5 text-[10px] font-bold text-muted-foreground">{t("llm.disabled")}</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{e.model}</TableCell>
                  <TableCell>
                    <div className="flex gap-1 justify-end">
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doToggleEndpointEnabled(e.name, "stt_endpoints")} disabled={!!busy} title={e.enabled === false ? t("llm.enable") : t("llm.disable")}>{e.enabled !== false ? <IconPower size={14} /> : <IconCircle size={14} />}</Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doStartEditSttEndpoint(e.name)} disabled={!!busy} title={t("llm.edit")}><IconEdit size={14} /></Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteSttEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* ── Add endpoint dialog ── */}
      <Dialog open={addEpDialogOpen} onOpenChange={(open) => { if (!open) setAddEpDialogOpen(false); }}>
        <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { resetEndpointEditor(); setConnTestResult(null); }}>
          <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
            <DialogTitle>{isEditingEndpoint ? t("llm.editEndpoint") : t("llm.addEndpoint")}</DialogTitle>
            <DialogDescription className="sr-only">{t("llm.addEndpoint")}</DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
            {/* Provider */}
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1">{t("llm.provider")} {!["custom", "ollama", "lmstudio"].includes(providerSlug) && <span className="inline-flex items-center gap-0.5 text-[11px] font-normal text-muted-foreground/70 min-w-0"><span className="shrink-0">{t("llm.baseUrlLabel")}</span><span className="inline-block max-w-[200px] overflow-x-auto whitespace-nowrap align-middle" style={{ scrollbarWidth: "thin" }}>{baseUrl || selectedProvider?.default_base_url || "—"}</span> <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] shrink-0" onClick={() => setBaseUrlExpanded(v => !v)}>{baseUrlExpanded ? t("llm.baseUrlCollapse") : t("llm.baseUrlToggle")}</Button></span>}</Label>
              <ProviderSearchSelect
                value={providerSlug}
                onChange={(v) => { setProviderSlug(v); setBaseUrlExpanded(false); }}
                options={providers.map((p) => ({ value: p.slug, label: p.name }))}
                placeholder={providers.length === 0 ? t("common.loading") : undefined}
                disabled={providers.length === 0}
              />
            </div>

            {/* Coding Plan toggle */}
            {selectedProvider?.coding_plan_base_url && (
              <label htmlFor="coding-plan-add" className="flex items-center justify-between gap-3 rounded-lg border border-border px-4 py-3 cursor-pointer select-none hover:bg-accent/50 transition-colors">
                <div className="space-y-0.5">
                  <div className="text-sm font-medium">{t("llm.codingPlan")}</div>
                  <div className="text-xs text-muted-foreground">{t("llm.codingPlanHint")}</div>
                </div>
                <Switch id="coding-plan-add" checked={codingPlanMode} onCheckedChange={(v) => { setCodingPlanMode(v); setBaseUrlTouched(false); }} />
              </label>
            )}

            {/* Base URL */}
            {["custom", "ollama", "lmstudio"].includes(providerSlug) ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setBaseUrlTouched(true); }} placeholder={selectedProvider?.default_base_url || "https://api.example.com/v1"} />
            </div>
            ) : baseUrlExpanded ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setBaseUrlTouched(true); }} placeholder={selectedProvider?.default_base_url || "https://api.example.com/v1"} />
            </div>
            ) : null}

            {/* API Key */}
            <div className="space-y-1.5">
              <Label className="inline-flex items-center gap-2">
                API Key {isLocalProvider(selectedProvider) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                {providerApplyUrl && !isLocalProvider(selectedProvider) && (
                  <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(providerApplyUrl)}>{t("llm.getApiKey")}</Button>
                )}
              </Label>
              <Input value={apiKeyValue} onChange={(e) => setApiKeyValue(e.target.value)} placeholder={isLocalProvider(selectedProvider) ? t("llm.localKeyPlaceholder") : t("llm.apiKeyPlaceholder")} type={(secretShown.__LLM_API_KEY && !IS_WEB) ? "text" : "password"} />
              {isLocalProvider(selectedProvider) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
            </div>

            {/* Model */}
            <div className="space-y-1.5">
              <Label>{t("llm.selectModel")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.modelHint")}<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchModels} disabled={(!apiKeyValue.trim() && !isLocalProvider(selectedProvider)) || !baseUrl.trim() || !!busy}>{t("llm.modelHintFetch")}</Button>{t("llm.modelHintSelect")}{models.length > 0 && <span className="text-muted-foreground/50">{t("llm.modelHintFetched", { count: models.length })}</span>}</span></Label>
              <SearchSelect
                value={selectedModelId}
                onChange={(v) => setSelectedModelId(v)}
                options={models.map((m) => m.id)}
                placeholder={models.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")}
                disabled={!!busy}
              />
            </div>

            {/* Endpoint Name */}
            <div className="space-y-1.5">
              <Label>{t("llm.endpointName")}</Label>
              <Input value={endpointName} onChange={(e) => { setEndpointNameTouched(true); setEndpointName(e.target.value); }} placeholder="dashscope-qwen3-max" />
            </div>

            {/* Capabilities */}
            <div className="space-y-1.5">
              <Label>{t("llm.capabilities")}</Label>
              <div className="flex flex-wrap gap-2">
                {[
                  { k: "text", name: t("llm.capText") },
                  { k: "thinking", name: t("llm.capThinking") },
                  { k: "vision", name: t("llm.capVision") },
                  { k: "video", name: t("llm.capVideo") },
                  { k: "tools", name: t("llm.capTools") },
                ].map((c) => {
                  const on = capSelected.includes(c.k);
                  return (
                    <button key={c.k} data-slot="cap-chip" type="button"
                      className={cn(
                        "inline-flex items-center justify-center h-8 px-3.5 rounded-md border text-sm font-medium cursor-pointer transition-colors",
                        on
                          ? "border-primary bg-primary text-primary-foreground shadow-sm hover:bg-primary/90"
                          : "border-input bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                      )}
                      onClick={() => { setCapTouched(true); setCapSelected((prev) => { const set = new Set(prev); if (set.has(c.k)) set.delete(c.k); else set.add(c.k); const out = Array.from(set); return out.length ? out : ["text"]; }); }}
                    >{c.name}</button>
                  );
                })}
              </div>
            </div>

            {/* Advanced (collapsed) */}
            <details className="group rounded-lg border border-border">
              <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium text-muted-foreground select-none list-none [&::-webkit-details-marker]:hidden hover:text-foreground transition-colors">
                <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90" />
                {t("llm.advancedParams") || t("llm.advanced") || "高级参数"}
              </summary>
              <div className="border-t border-border px-4 py-3 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label>{t("llm.advApiType")}</Label>
                    <Select value={apiType} onValueChange={(v) => setApiType(v as any)}>
                      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="openai">openai</SelectItem>
                        <SelectItem value="openai_responses">openai_responses</SelectItem>
                        <SelectItem value="anthropic">anthropic</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advPriority")}</Label>
                    <Input type="number" value={String(endpointPriority)} onChange={(e) => setEndpointPriority(Number(e.target.value))} />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advMaxTokens")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advMaxTokensHint")}</span></Label>
                  <Input type="number" min={0} value={addEpMaxTokens} onChange={(e) => setAddEpMaxTokens(Math.max(0, parseInt(e.target.value) || 0))} />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advContextWindow")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advContextWindowHint")}</span></Label>
                  <Input type="number" min={0} value={addEpContextWindow ? Math.round(addEpContextWindow / 1000) : ""} onChange={(e) => setAddEpContextWindow((parseInt(e.target.value) || 0) * 1000)} />
                  {addEpContextWindow > 0 && addEpContextWindow < 60000 && (
                    <p className="flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400 font-medium">
                      <AlertTriangle className="size-3 shrink-0" />
                      {t("llm.advContextWindowWarn")}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advTimeout")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advTimeoutHint")}</span></Label>
                  <Input type="number" min={10} value={addEpTimeout} onChange={(e) => setAddEpTimeout(Math.max(10, parseInt(e.target.value) || 180))} />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advRpmLimit")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advRpmLimitHint")}</span></Label>
                  <Input type="number" min={0} value={addEpRpmLimit} onChange={(e) => setAddEpRpmLimit(Math.max(0, parseInt(e.target.value) || 0))} />
                </div>
              </div>
            </details>
          </div>

          {connTestResult && (
            <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
              connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
            )}>
              {connTestResult.ok
                ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
            </div>
          )}

          <DialogFooter className="px-6 py-2.5 shrink-0 flex-col sm:flex-col gap-1.5">
            <div className="flex items-center justify-between w-full">
              <Button variant="ghost" onClick={() => setAddEpDialogOpen(false)}>{t("common.cancel")}</Button>
              <div className="flex gap-2 items-center">
                <Button variant="secondary"
                  disabled={(!apiKeyValue.trim() && !isLocalProvider(selectedProvider)) || !baseUrl.trim() || connTesting}
                  onClick={() => doTestConnection({ testApiType: apiType, testBaseUrl: baseUrl, testApiKey: apiKeyValue.trim() || (isLocalProvider(selectedProvider) ? localProviderPlaceholderKey(selectedProvider) : ""), testProviderSlug: selectedProvider?.slug })}
                >
                  {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                </Button>
                {(() => {
                  const _isLocal = isLocalProvider(selectedProvider);
                  const missing: string[] = [];
                  if (!baseUrl.trim()) missing.push("Base URL");
                  if (!_isLocal && !apiKeyValue.trim()) missing.push("API Key");
                  if (!selectedModelId.trim()) missing.push(t("status.model"));
                  if (!currentWorkspaceId && dataMode !== "remote") missing.push(t("workspace.title") || "工作区");
                  const btnDisabled = missing.length > 0 || !!busy;
                  return (
                    <Button onClick={async () => { const ok = await doSaveEndpoint(); if (ok) { setAddEpDialogOpen(false); setConnTestResult(null); } }} disabled={btnDisabled}>
                      {isEditingEndpoint ? t("common.save") : t("llm.addEndpoint")}
                    </Button>
                  );
                })()}
              </div>
            </div>
            {(() => {
              const _isLocal = isLocalProvider(selectedProvider);
              const missing: string[] = [];
              if (!baseUrl.trim()) missing.push("Base URL");
              if (!_isLocal && !apiKeyValue.trim()) missing.push("API Key");
              if (!selectedModelId.trim()) missing.push(t("status.model"));
              if (!currentWorkspaceId && dataMode !== "remote") missing.push(t("workspace.title") || "工作区");
              const show = missing.length > 0 && !busy;
              return (
                <div className={cn("text-[10px] text-muted-foreground text-right w-full", !show && "invisible")}>{t("common.missingFields") || "缺少"}: {missing.join(", ") || "—"}</div>
              );
            })()}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Edit endpoint modal ── */}
      <Dialog open={editModalOpen && !!editDraft} onOpenChange={(open) => { if (!open) setEditModalOpen(false); }}>
        <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { resetEndpointEditor(); setConnTestResult(null); }}>
          <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
            <DialogTitle>{editEndpointType === "compiler_endpoints" ? t("llm.editCompiler") : editEndpointType === "stt_endpoints" ? t("llm.editStt") : t("llm.editEndpoint")}: {editDraft?.name}</DialogTitle>
            <DialogDescription className="sr-only">{t("llm.editEndpoint")}</DialogDescription>
          </DialogHeader>

          {editDraft && <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
            {/* Provider (read-only) */}
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1 flex-wrap">{t("llm.provider")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.providerReadonly")}</span> {!["custom", "ollama", "lmstudio"].includes(editDraft.providerSlug) && <span className="inline-flex items-center gap-0.5 text-[11px] font-normal text-muted-foreground/70 min-w-0"><span className="shrink-0">{t("llm.baseUrlLabel")}</span><span className="inline-block max-w-[200px] overflow-x-auto whitespace-nowrap align-middle" style={{ scrollbarWidth: "thin" }}>{editDraft.baseUrl || "—"}</span> <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] shrink-0" onClick={() => setEditBaseUrlExpanded(v => !v)}>{editBaseUrlExpanded ? t("llm.baseUrlCollapse") : t("llm.baseUrlToggle")}</Button></span>}</Label>
              <Input value={(() => { const p = providers.find((x) => x.slug === editDraft.providerSlug); return p ? p.name : (editDraft.providerSlug || "custom"); })()} disabled className="opacity-70" />
            </div>

            {/* Base URL */}
            {["custom", "ollama", "lmstudio"].includes(editDraft.providerSlug) ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={editDraft.baseUrl || ""} onChange={(e) => setEditDraft({ ...editDraft, baseUrl: e.target.value })} placeholder="请输入" />
            </div>
            ) : editBaseUrlExpanded ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={editDraft.baseUrl || ""} onChange={(e) => setEditDraft({ ...editDraft, baseUrl: e.target.value })} placeholder="请输入" />
            </div>
            ) : null}

            {/* API Key */}
            <div className="space-y-1.5">
              <Label className="inline-flex items-center gap-2">
                API Key {isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                {(() => { const url = getProviderApplyUrl(editDraft.providerSlug); const ep = providers.find((p) => p.slug === editDraft.providerSlug); return url && !isLocalProvider(ep) ? <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(url)}>{t("llm.getApiKey")}</Button> : null; })()}
              </Label>
              <div className="relative">
                <Input value={editDraft.apiKeyValue} onChange={(e) => { setEditDraft((d) => d ? { ...d, apiKeyValue: e.target.value } : d); }} type={(secretShown.__EDIT_EP_KEY && !IS_WEB) ? "text" : "password"} className="pr-11" placeholder={isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) ? t("llm.localKeyPlaceholder") : t("llm.apiKeyPlaceholder")} />
                {!IS_WEB && <Button type="button" variant="ghost" size="icon-xs" className="absolute right-1.5 top-1/2 -translate-y-1/2" onClick={() => setSecretShown((m) => ({ ...m, __EDIT_EP_KEY: !m.__EDIT_EP_KEY }))} title={secretShown.__EDIT_EP_KEY ? t("llm.hideSecret") : t("llm.showSecret")}>
                  {secretShown.__EDIT_EP_KEY ? <IconEyeOff size={14} /> : <IconEye size={14} />}
                </Button>}
              </div>
              {isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
            </div>

            {/* Model */}
            <div className="space-y-1.5">
              <Label>{t("status.model")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.modelHint")}<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchEditModels} disabled={(!isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) && !(editDraft.apiKeyValue || "").trim()) || !(editDraft.baseUrl || "").trim() || !!busy}>{t("llm.modelHintFetch")}</Button>{t("llm.modelHintSelect")}{editModels.length > 0 && <span className="text-muted-foreground/50">{t("llm.modelHintFetched", { count: editModels.length })}</span>}</span></Label>
              <SearchSelect
                value={editDraft.modelId || ""}
                onChange={(v) => setEditDraft({ ...editDraft, modelId: v })}
                options={editModels.length > 0 ? editModels.map(m => m.id) : [editDraft.modelId || ""].filter(Boolean)}
                placeholder={editModels.length > 0 ? t("llm.searchModel") : (editDraft.modelId || t("llm.modelPlaceholder"))}
                disabled={!!busy}
              />
            </div>

            {editEndpointType === "endpoints" && <>
            {/* Capabilities */}
            <div className="space-y-1.5">
              <Label>{t("llm.capabilities")}</Label>
              <div className="flex flex-wrap gap-2">
                {[
                  { k: "text", name: t("llm.capText") },
                  { k: "thinking", name: t("llm.capThinking") },
                  { k: "vision", name: t("llm.capVision") },
                  { k: "video", name: t("llm.capVideo") },
                  { k: "tools", name: t("llm.capTools") },
                ].map((c) => {
                  const on = (editDraft.caps || []).includes(c.k);
                  return (
                    <button key={c.k} data-slot="cap-chip" type="button"
                      className={cn(
                        "inline-flex items-center justify-center h-8 px-3.5 rounded-md border text-sm font-medium cursor-pointer transition-colors",
                        on
                          ? "border-primary bg-primary text-primary-foreground shadow-sm hover:bg-primary/90"
                          : "border-input bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                      )}
                      onClick={() => setEditDraft((d) => {
                        if (!d) return d;
                        const set = new Set(d.caps || []);
                        if (set.has(c.k)) set.delete(c.k); else set.add(c.k);
                        const out = Array.from(set);
                        return { ...d, caps: out.length ? out : ["text"] };
                      })}
                    >{c.name}</button>
                  );
                })}
              </div>
            </div>

            {/* Advanced (collapsed) */}
            <details className="group rounded-lg border border-border">
              <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium text-muted-foreground select-none list-none [&::-webkit-details-marker]:hidden hover:text-foreground transition-colors">
                <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90" />
                {t("llm.advancedParams") || t("llm.advanced") || "高级参数"}
              </summary>
              <div className="border-t border-border px-4 py-3 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label>{t("llm.advApiType")}</Label>
                    <Select value={editDraft.apiType} onValueChange={(v) => setEditDraft({ ...editDraft, apiType: v as any })}>
                      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="openai">openai</SelectItem>
                        <SelectItem value="openai_responses">openai_responses</SelectItem>
                        <SelectItem value="anthropic">anthropic</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advPriority")}</Label>
                    <Input type="number" value={editDraft.priority} onChange={(e) => setEditDraft({ ...editDraft, priority: Number(e.target.value) || 1 })} />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advMaxTokens")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advMaxTokensHint")}</span></Label>
                  <Input type="number" min={0} value={editDraft.maxTokens} onChange={(e) => setEditDraft({ ...editDraft, maxTokens: Math.max(0, parseInt(e.target.value) || 0) })} />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advContextWindow")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advContextWindowHint")}</span></Label>
                  <Input type="number" min={0} value={editDraft.contextWindow ? Math.round(editDraft.contextWindow / 1000) : ""} onChange={(e) => setEditDraft({ ...editDraft, contextWindow: (parseInt(e.target.value) || 0) * 1000 })} />
                  {editDraft.contextWindow > 0 && editDraft.contextWindow < 60000 && (
                    <p className="flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400 font-medium">
                      <AlertTriangle className="size-3 shrink-0" />
                      {t("llm.advContextWindowWarn")}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advTimeout")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advTimeoutHint")}</span></Label>
                  <Input type="number" min={10} value={editDraft.timeout} onChange={(e) => setEditDraft({ ...editDraft, timeout: Math.max(10, parseInt(e.target.value) || 180) })} />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("llm.advRpmLimit")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advRpmLimitHint")}</span></Label>
                  <Input type="number" min={0} value={editDraft.rpmLimit} onChange={(e) => setEditDraft({ ...editDraft, rpmLimit: Math.max(0, parseInt(e.target.value) || 0) })} />
                </div>
              </div>
            </details>

            {/* 阶梯定价配置 */}
            <details className="group rounded-lg border border-border">
              <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium text-muted-foreground select-none list-none [&::-webkit-details-marker]:hidden hover:text-foreground transition-colors">
                <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90" />
                {t("llm.pricingConfig")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.pricingConfigHint")}</span>
              </summary>
              <div className="border-t border-border px-4 py-3 space-y-2.5">
                {(editDraft.pricingTiers || []).length > 0 && (
                  <div className="grid grid-cols-[1fr_1fr_1fr_28px] gap-1.5 text-[11px] text-muted-foreground">
                    <span>最大输入 tokens</span>
                    <span>输入价格/M</span>
                    <span>输出价格/M</span>
                    <span />
                  </div>
                )}
                {(editDraft.pricingTiers || []).map((tier, idx) => (
                  <div key={idx} className="grid grid-cols-[1fr_1fr_1fr_28px] gap-1.5 items-center">
                    <Input type="number" min={0} placeholder="128000" value={tier.max_input || ""} onChange={(e) => {
                      const tiers = [...(editDraft.pricingTiers || [])];
                      tiers[idx] = { ...tiers[idx], max_input: parseInt(e.target.value) || 0 };
                      setEditDraft({ ...editDraft, pricingTiers: tiers });
                    }} className="h-8 text-xs" />
                    <Input type="number" min={0} step={0.01} placeholder="1.2" value={tier.input_price || ""} onChange={(e) => {
                      const tiers = [...(editDraft.pricingTiers || [])];
                      tiers[idx] = { ...tiers[idx], input_price: parseFloat(e.target.value) || 0 };
                      setEditDraft({ ...editDraft, pricingTiers: tiers });
                    }} className="h-8 text-xs" />
                    <Input type="number" min={0} step={0.01} placeholder="7.2" value={tier.output_price || ""} onChange={(e) => {
                      const tiers = [...(editDraft.pricingTiers || [])];
                      tiers[idx] = { ...tiers[idx], output_price: parseFloat(e.target.value) || 0 };
                      setEditDraft({ ...editDraft, pricingTiers: tiers });
                    }} className="h-8 text-xs" />
                    <Button data-slot="pricing-btn" variant="ghost" size="icon-xs" className="text-muted-foreground/50 hover:text-destructive" onClick={() => {
                      const tiers = (editDraft.pricingTiers || []).filter((_, i) => i !== idx);
                      setEditDraft({ ...editDraft, pricingTiers: tiers });
                    }}><XIcon className="size-3.5" /></Button>
                  </div>
                ))}
                <Button data-slot="pricing-btn" variant="outline" size="sm" className="w-full border-dashed text-muted-foreground text-xs" onClick={() => {
                  const tiers = [...(editDraft.pricingTiers || []), { max_input: 0, input_price: 0, output_price: 0 }];
                  setEditDraft({ ...editDraft, pricingTiers: tiers });
                }}>
                  + 添加档位
                </Button>
              </div>
            </details>
            </>}

            {/* Endpoint name (for compiler/STT) */}
            {editEndpointType !== "endpoints" && (
            <div className="space-y-1.5">
              <Label>{t("llm.endpointName")}</Label>
              <Input value={editDraft.name} onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })} />
            </div>
            )}
          </div>}

          {connTestResult && (
            <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
              connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
            )}>
              {connTestResult.ok
                ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
            </div>
          )}

          <DialogFooter className="px-6 py-2.5 shrink-0 flex-row justify-between sm:justify-between">
            <Button variant="ghost" onClick={() => setEditModalOpen(false)}>{t("common.cancel")}</Button>
            <div className="flex gap-2 items-center">
              <Button variant="secondary"
                disabled={(!isLocalProvider(providers.find((p) => p.slug === editDraft?.providerSlug)) && !(editDraft?.apiKeyValue || "").trim()) || !(editDraft?.baseUrl || "").trim() || connTesting}
                onClick={() => { const _ep = providers.find((p) => p.slug === editDraft?.providerSlug); doTestConnection({
                  testApiType: editDraft?.apiType || "openai",
                  testBaseUrl: editDraft?.baseUrl || "",
                  testApiKey: (editDraft?.apiKeyValue || "").trim() || (isLocalProvider(_ep) ? localProviderPlaceholderKey(_ep) : ""),
                  testProviderSlug: editDraft?.providerSlug,
                }); }}
              >
                {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
              </Button>
              <Button onClick={async () => { await doSaveEditedEndpoint(); }} disabled={!!busy}>{t("common.save")}</Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Add compiler dialog ── */}
      <Dialog open={addCompDialogOpen} onOpenChange={(open) => { if (!open) setAddCompDialogOpen(false); }}>
        <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { setConnTestResult(null); }}>
          <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
            <DialogTitle>{t("llm.addCompiler")}</DialogTitle>
            <DialogDescription className="sr-only">{t("llm.addCompiler")}</DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
            {/* Provider */}
            <div className="space-y-1.5">
              <Label>{t("llm.provider")} {!["custom", "ollama", "lmstudio"].includes(compilerProviderSlug) && <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlLabel")}{compilerBaseUrl || "—"} <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => setCompBaseUrlExpanded(v => !v)}>{compBaseUrlExpanded ? t("llm.baseUrlCollapse") : t("llm.baseUrlToggle")}</Button></span>}</Label>
              <ProviderSearchSelect
                value={compilerProviderSlug}
                onChange={(slug) => {
                  setCompilerProviderSlug(slug);
                  setCompBaseUrlExpanded(false);
                  setCompilerCodingPlan(false);
                  if (slug === "custom") {
                    setCompilerApiType("openai");
                    setCompilerBaseUrl("");
                    setCompilerApiKeyValue("");
                  } else {
                    const p = providers.find((x) => x.slug === slug);
                    if (p) {
                      setCompilerApiType((p.api_type as any) || "openai");
                      setCompilerBaseUrl(p.default_base_url || "");
                      if (isLocalProvider(p)) {
                        setCompilerApiKeyValue(localProviderPlaceholderKey(p));
                      } else {
                        setCompilerApiKeyValue("");
                      }
                    }
                  }
                }}
                options={providers.map((p) => ({ value: p.slug, label: p.name }))}
              />
            </div>

            {/* Coding Plan toggle */}
            {(() => { const cp = providers.find((x) => x.slug === compilerProviderSlug); return cp?.coding_plan_base_url ? (
              <label htmlFor="coding-plan-comp" className="flex items-center justify-between gap-3 rounded-lg border border-border px-4 py-3 cursor-pointer select-none hover:bg-accent/50 transition-colors">
                <div className="space-y-0.5">
                  <div className="text-sm font-medium">{t("llm.codingPlan")}</div>
                  <div className="text-xs text-muted-foreground">{t("llm.codingPlanHint")}</div>
                </div>
                <Switch id="coding-plan-comp" checked={compilerCodingPlan} onCheckedChange={(v) => {
                  setCompilerCodingPlan(v);
                  if (cp) {
                    if (v && cp.coding_plan_base_url) {
                      setCompilerBaseUrl(cp.coding_plan_base_url);
                      setCompilerApiType((cp.coding_plan_api_type as "openai" | "anthropic") || "anthropic");
                    } else {
                      setCompilerBaseUrl(cp.default_base_url || "");
                      setCompilerApiType((cp.api_type as "openai" | "anthropic") || "openai");
                    }
                  }
                }} />
              </label>
            ) : null; })()}

            {/* Base URL */}
            {["custom", "ollama", "lmstudio"].includes(compilerProviderSlug) ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={compilerBaseUrl} onChange={(e) => setCompilerBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
            </div>
            ) : compBaseUrlExpanded ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={compilerBaseUrl} onChange={(e) => setCompilerBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
            </div>
            ) : null}

            {/* API Key */}
            <div className="space-y-1.5">
              <Label className="inline-flex items-center gap-2">
                API Key {isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug)) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                {(() => { const url = getProviderApplyUrl(compilerProviderSlug); const cp = providers.find((p) => p.slug === compilerProviderSlug); return url && !isLocalProvider(cp) ? <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(url)}>{t("llm.getApiKey")}</Button> : null; })()}
              </Label>
              <Input value={compilerApiKeyValue} onChange={(e) => setCompilerApiKeyValue(e.target.value)} placeholder={isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug)) ? t("llm.localKeyPlaceholder") : t("llm.apiKeyPlaceholder")} type="password" />
              {isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug)) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
            </div>

            {/* Model */}
            <div className="space-y-1.5">
              <Label>{t("status.model")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.modelHint")}<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchCompilerModels} disabled={(!compilerApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug))) || !compilerBaseUrl.trim() || !!busy}>{t("llm.modelHintFetch")}</Button>{t("llm.modelHintSelect")}{compilerModels.length > 0 && <span className="text-muted-foreground/50">{t("llm.modelHintFetched", { count: compilerModels.length })}</span>}</span></Label>
              <SearchSelect value={compilerModel} onChange={(v) => setCompilerModel(v)} options={compilerModels.map((m) => m.id)} placeholder={compilerModels.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")} disabled={!!busy} />
            </div>

            {/* Endpoint Name */}
            <div className="space-y-1.5">
              <Label>{t("llm.endpointName")} <span className="text-[11px] font-normal text-muted-foreground/70">({t("common.optional")})</span></Label>
              <Input value={compilerEndpointName} onChange={(e) => setCompilerEndpointName(e.target.value)} placeholder={`compiler-${compilerProviderSlug || "custom"}-${compilerModel || "model"}`} />
            </div>
          </div>

          {connTestResult && (
            <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
              connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
            )}>
              {connTestResult.ok
                ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
            </div>
          )}

          <DialogFooter className="px-6 py-2.5 shrink-0 flex-col sm:flex-col gap-1.5">
            <div className="flex items-center justify-between w-full">
              <Button variant="ghost" onClick={() => setAddCompDialogOpen(false)}>{t("common.cancel")}</Button>
              <div className="flex gap-2 items-center">
                <Button variant="secondary"
                  disabled={(!compilerApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug))) || !compilerBaseUrl.trim() || connTesting}
                  onClick={() => { const _cp = providers.find((p) => p.slug === compilerProviderSlug); doTestConnection({
                    testApiType: compilerApiType,
                    testBaseUrl: compilerBaseUrl,
                    testApiKey: compilerApiKeyValue.trim() || (isLocalProvider(_cp) ? localProviderPlaceholderKey(_cp) : ""),
                    testProviderSlug: compilerProviderSlug || null,
                  }); }}
                >
                  {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                </Button>
                {(() => {
                  const _isCompLocal = isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug));
                  const cMissing: string[] = [];
                  if (!compilerModel.trim()) cMissing.push(t("status.model"));
                  if (!_isCompLocal && !compilerApiKeyValue.trim()) cMissing.push("API Key");
                  if (!currentWorkspaceId && dataMode !== "remote") cMissing.push(t("workspace.title") || "工作区");
                  const cBtnDisabled = cMissing.length > 0 || !!busy;
                  return (
                    <Button onClick={async () => { const ok = await doSaveCompilerEndpoint(); if (ok) { setAddCompDialogOpen(false); setConnTestResult(null); } }} disabled={cBtnDisabled}>
                      {t("llm.addEndpoint")}
                    </Button>
                  );
                })()}
              </div>
            </div>
            {(() => {
              const _isCompLocal = isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug));
              const cMissing: string[] = [];
              if (!compilerModel.trim()) cMissing.push(t("status.model"));
              if (!_isCompLocal && !compilerApiKeyValue.trim()) cMissing.push("API Key");
              if (!currentWorkspaceId && dataMode !== "remote") cMissing.push(t("workspace.title") || "工作区");
              const cShow = cMissing.length > 0 && !busy;
              return (
                <div className={cn("text-[10px] text-muted-foreground text-right w-full", !cShow && "invisible")}>{t("common.missingFields") || "缺少"}: {cMissing.join(", ") || "—"}</div>
              );
            })()}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Add STT dialog ── */}
      <Dialog open={addSttDialogOpen} onOpenChange={(open) => { if (!open) setAddSttDialogOpen(false); }}>
        <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { setConnTestResult(null); }}>
          <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
            <DialogTitle>{t("llm.addStt")}</DialogTitle>
            <DialogDescription className="sr-only">{t("llm.addStt")}</DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
            {/* Provider */}
            <div className="space-y-1.5">
              <Label>{t("llm.provider")} {!["custom", "ollama", "lmstudio"].includes(sttProviderSlug) && <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlLabel")}{sttBaseUrl || "—"} <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => setSttBaseUrlExpanded(v => !v)}>{sttBaseUrlExpanded ? t("llm.baseUrlCollapse") : t("llm.baseUrlToggle")}</Button></span>}</Label>
              <ProviderSearchSelect
                value={sttProviderSlug}
                onChange={(slug) => {
                  setSttBaseUrlExpanded(false);
                  setSttProviderSlug(slug);
                  if (slug === "custom") {
                    setSttApiType("openai");
                    setSttBaseUrl("");
                    setSttApiKeyValue("");
                    setSttModels([]);
                    setSttModel("");
                  } else {
                    const p = providers.find((x) => x.slug === slug);
                    if (p) {
                      setSttApiType((p.api_type as any) || "openai");
                      setSttBaseUrl(p.default_base_url || "");
                      if (isLocalProvider(p)) {
                        setSttApiKeyValue(localProviderPlaceholderKey(p));
                      } else {
                        setSttApiKeyValue("");
                      }
                    }
                    const rec = STT_RECOMMENDED_MODELS[slug];
                    if (rec?.length) {
                      setSttModels(rec.map((m) => ({ id: m.id, name: m.id, capabilities: {} })));
                      setSttModel(rec[0].id);
                    } else {
                      setSttModels([]);
                      setSttModel("");
                    }
                  }
                }}
                options={providers.map((p) => ({ value: p.slug, label: p.name }))}
              />
            </div>

            {/* Base URL */}
            {["custom", "ollama", "lmstudio"].includes(sttProviderSlug) ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={sttBaseUrl} onChange={(e) => setSttBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
            </div>
            ) : sttBaseUrlExpanded ? (
            <div className="space-y-1.5">
              <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
              <Input value={sttBaseUrl} onChange={(e) => setSttBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
            </div>
            ) : null}

            {/* API Key */}
            <div className="space-y-1.5">
              <Label className="inline-flex items-center gap-2">
                API Key {isLocalProvider(providers.find((p) => p.slug === sttProviderSlug)) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                {(() => { const url = getProviderApplyUrl(sttProviderSlug); const sp = providers.find((p) => p.slug === sttProviderSlug); return url && !isLocalProvider(sp) ? <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(url)}>{t("llm.getApiKey")}</Button> : null; })()}
              </Label>
              <Input value={sttApiKeyValue} onChange={(e) => setSttApiKeyValue(e.target.value)} placeholder={isLocalProvider(providers.find((p) => p.slug === sttProviderSlug)) ? t("llm.localKeyPlaceholder") : t("llm.apiKeyPlaceholder")} type="password" />
              {isLocalProvider(providers.find((p) => p.slug === sttProviderSlug)) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
            </div>

            {/* Model */}
            <div className="space-y-1.5">
              <Label>{t("status.model")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.modelHint")}<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchSttModels} disabled={(!sttApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === sttProviderSlug))) || !sttBaseUrl.trim() || !!busy}>{t("llm.modelHintFetch")}</Button>{t("llm.modelHintSelect")}{sttModels.length > 0 && <span className="text-muted-foreground/50">{t("llm.modelHintFetched", { count: sttModels.length })}</span>}</span></Label>
              <SearchSelect value={sttModel} onChange={(v) => setSttModel(v)} options={sttModels.map((m) => m.id)} placeholder={sttModels.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")} disabled={!!busy} />
              {(() => {
                const rec = STT_RECOMMENDED_MODELS[sttProviderSlug];
                if (!rec?.length) return null;
                return (
                  <div className="mt-1 text-xs text-muted-foreground/70 leading-relaxed">
                    {rec.map((m) => (
                      <span key={m.id} className="mr-3">
                        <code className="bg-muted/50 px-1.5 py-0.5 rounded cursor-pointer hover:bg-muted transition-colors" onClick={() => setSttModel(m.id)}>{m.id}</code>
                        {m.note && <span className="ml-1 text-primary">{m.note}</span>}
                      </span>
                    ))}
                  </div>
                );
              })()}
            </div>

            {/* Endpoint Name */}
            <div className="space-y-1.5">
              <Label>{t("llm.endpointName")} <span className="text-[11px] font-normal text-muted-foreground/70">({t("common.optional")})</span></Label>
              <Input value={sttEndpointName} onChange={(e) => setSttEndpointName(e.target.value)} placeholder={`stt-${sttProviderSlug || "custom"}-${sttModel || "model"}`} />
            </div>
          </div>

          {connTestResult && (
            <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
              connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
            )}>
              {connTestResult.ok
                ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
            </div>
          )}

          <DialogFooter className="px-6 py-2.5 shrink-0 flex-col sm:flex-col gap-1.5">
            <div className="flex items-center justify-between w-full">
              <Button variant="ghost" onClick={() => setAddSttDialogOpen(false)}>{t("common.cancel")}</Button>
              <div className="flex gap-2 items-center">
                <Button variant="secondary"
                  disabled={(!sttApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === sttProviderSlug))) || !sttBaseUrl.trim() || connTesting}
                  onClick={() => { const _sp = providers.find((p) => p.slug === sttProviderSlug); doTestConnection({
                    testApiType: sttApiType,
                    testBaseUrl: sttBaseUrl,
                    testApiKey: sttApiKeyValue.trim() || (isLocalProvider(_sp) ? localProviderPlaceholderKey(_sp) : ""),
                    testProviderSlug: sttProviderSlug || null,
                  }); }}
                >
                  {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                </Button>
                {(() => {
                  const _isSttLocal = isLocalProvider(providers.find((p) => p.slug === sttProviderSlug));
                  const sMissing: string[] = [];
                  if (!sttModel.trim()) sMissing.push(t("status.model"));
                  if (!_isSttLocal && !sttApiKeyValue.trim()) sMissing.push("API Key");
                  if (!currentWorkspaceId && dataMode !== "remote") sMissing.push(t("workspace.title") || "工作区");
                  const sBtnDisabled = sMissing.length > 0 || !!busy;
                  return (
                    <Button onClick={async () => { const ok = await doSaveSttEndpoint(); if (ok) { setAddSttDialogOpen(false); setConnTestResult(null); } }} disabled={sBtnDisabled}>
                      {t("llm.addStt")}
                    </Button>
                  );
                })()}
              </div>
            </div>
            {(() => {
              const _isSttLocal = isLocalProvider(providers.find((p) => p.slug === sttProviderSlug));
              const sMissing: string[] = [];
              if (!sttModel.trim()) sMissing.push(t("status.model"));
              if (!_isSttLocal && !sttApiKeyValue.trim()) sMissing.push("API Key");
              if (!currentWorkspaceId && dataMode !== "remote") sMissing.push(t("workspace.title") || "工作区");
              const sShow = sMissing.length > 0 && !busy;
              return (
                <div className={cn("text-[10px] text-muted-foreground text-right w-full", !sShow && "invisible")}>{t("common.missingFields") || "缺少"}: {sMissing.join(", ") || "—"}</div>
              );
            })()}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
