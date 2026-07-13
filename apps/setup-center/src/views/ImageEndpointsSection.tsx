import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImageIcon, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { DotGray, DotGreen, IconChevronUp, IconCircle, IconEdit, IconPower, IconTrash } from "../icons";
import { safeFetch } from "../providers";
import type { EndpointDraft, EnvMap } from "../types";
import { notifyError, notifyLoading, notifySuccess, dismissLoading } from "../utils/notify";

type ImageProtocol = "dashscope" | "openai_images";

type ImageEndpointForm = {
  name: string;
  provider: string;
  apiType: ImageProtocol;
  baseUrl: string;
  apiKey: string;
  apiKeyEnv: string;
  model: string;
  timeout: number;
  defaultSize: string;
  defaultQuality: string;
};

const PRESETS: Record<string, Omit<ImageEndpointForm, "name" | "apiKey" | "apiKeyEnv" | "timeout" | "defaultSize" | "defaultQuality"> & { defaultSize: string }> = {
  dashscope: {
    provider: "dashscope",
    apiType: "dashscope",
    baseUrl: "https://dashscope.aliyuncs.com",
    model: "qwen-image-max",
    defaultSize: "1664*928",
  },
  openai: {
    provider: "openai",
    apiType: "openai_images",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-image-1",
    defaultSize: "1024x1024",
  },
  custom: {
    provider: "custom",
    apiType: "openai_images",
    baseUrl: "",
    model: "",
    defaultSize: "1024x1024",
  },
};

function emptyForm(): ImageEndpointForm {
  return {
    name: "",
    provider: PRESETS.dashscope.provider,
    apiType: PRESETS.dashscope.apiType,
    baseUrl: PRESETS.dashscope.baseUrl,
    apiKey: "",
    apiKeyEnv: "",
    model: PRESETS.dashscope.model,
    timeout: 180,
    defaultSize: PRESETS.dashscope.defaultSize,
    defaultQuality: "",
  };
}

export interface ImageEndpointsSectionProps {
  endpoints: EndpointDraft[];
  envDraft: EnvMap;
  disabled: boolean;
  disabledMessage: string;
  httpApiBase: () => string;
  reloadEndpoints: () => Promise<void>;
  askConfirm: (message: string, onConfirm: () => void) => void;
}

export function ImageEndpointsSection({
  endpoints,
  envDraft,
  disabled,
  disabledMessage,
  httpApiBase,
  reloadEndpoints,
  askConfirm,
}: ImageEndpointsSectionProps) {
  const { t } = useTranslation();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [originalName, setOriginalName] = useState<string | null>(null);
  const [form, setForm] = useState<ImageEndpointForm>(emptyForm);
  const [selectedNames, setSelectedNames] = useState<Set<string>>(() => new Set());
  const nextPriority = useMemo(
    () => endpoints.reduce((max, endpoint) => Math.max(max, endpoint.priority || 0), 0) + 10,
    [endpoints],
  );

  useEffect(() => {
    const available = new Set(endpoints.map((endpoint) => endpoint.name));
    setSelectedNames((current) => new Set(Array.from(current).filter((name) => available.has(name))));
  }, [endpoints]);

  function startAdd() {
    setOriginalName(null);
    setForm(emptyForm());
    setDialogOpen(true);
  }

  function startEdit(endpoint: EndpointDraft) {
    const extra = endpoint.extra_params || {};
    setOriginalName(endpoint.name);
    setForm({
      name: endpoint.name,
      provider: endpoint.provider,
      apiType: endpoint.api_type as ImageProtocol,
      baseUrl: endpoint.base_url,
      apiKey: "",
      apiKeyEnv: endpoint.api_key_env,
      model: endpoint.model,
      timeout: endpoint.timeout || 180,
      defaultSize: String(extra.default_size || "1024x1024"),
      defaultQuality: String(extra.default_quality || ""),
    });
    setDialogOpen(true);
  }

  function applyPreset(provider: string) {
    const preset = PRESETS[provider] || PRESETS.custom;
    setForm((current) => ({
      ...current,
      provider: preset.provider,
      apiType: preset.apiType,
      baseUrl: preset.baseUrl,
      model: preset.model,
      defaultSize: preset.defaultSize,
    }));
  }

  async function save() {
    if (!form.name.trim() || !form.baseUrl.trim() || !form.model.trim()) {
      notifyError(t("llm.imageRequired"));
      return;
    }
    if (!/^https?:\/\//i.test(form.baseUrl.trim())) {
      notifyError(t("llm.imageBaseUrlInvalid"));
      return;
    }
    if (!originalName && !form.apiKey.trim()) {
      notifyError(t("llm.imageApiKeyRequired"));
      return;
    }

    const busyId = notifyLoading(t("llm.imageSaving"));
    try {
      const extraParams: Record<string, string> = {};
      if (form.defaultSize.trim()) extraParams.default_size = form.defaultSize.trim();
      if (form.defaultQuality.trim()) extraParams.default_quality = form.defaultQuality.trim();
      const endpoint = {
        name: form.name.trim(),
        provider: form.provider,
        api_type: form.apiType,
        base_url: form.baseUrl.trim(),
        api_key_env: form.apiKeyEnv,
        model: form.model.trim(),
        priority: originalName
          ? endpoints.find((item) => item.name === originalName)?.priority || nextPriority
          : nextPriority,
        timeout: Math.max(1, Number(form.timeout) || 180),
        capabilities: ["image_generation"],
        extra_params: extraParams,
      };
      const response = await safeFetch(`${httpApiBase()}/api/config/save-endpoint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint,
          api_key: form.apiKey.trim() || null,
          endpoint_type: "image_endpoints",
          original_name: originalName,
        }),
      });
      const data = await response.json();
      if (data.status !== "ok") throw new Error(data.error || t("llm.imageSaveFailed"));
      await reloadEndpoints();
      setDialogOpen(false);
      notifySuccess(t("llm.imageSaved"));
    } catch (error) {
      notifyError(String((error as Error)?.message || error));
    } finally {
      dismissLoading(busyId);
    }
  }

  async function mutate(path: string, init: RequestInit) {
    try {
      const response = await safeFetch(`${httpApiBase()}${path}`, init);
      const data = await response.json();
      if (data.status !== "ok") throw new Error(data.error || t("llm.imageSaveFailed"));
      await reloadEndpoints();
    } catch (error) {
      notifyError(String((error as Error)?.message || error));
    }
  }

  function moveUp(name: string) {
    const names = endpoints.map((endpoint) => endpoint.name);
    const index = names.indexOf(name);
    if (index <= 0) return;
    [names[index - 1], names[index]] = [names[index], names[index - 1]];
    void mutate("/api/config/reorder-endpoints", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ordered_names: names, endpoint_type: "image_endpoints" }),
    });
  }

  function toggleSelected(name: string) {
    setSelectedNames((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function setAllSelected(checked: boolean) {
    setSelectedNames(checked ? new Set(endpoints.map((endpoint) => endpoint.name)) : new Set());
  }

  async function deleteSelected() {
    const names = Array.from(selectedNames);
    if (names.length === 0) return;
    const busyId = notifyLoading(t("llm.imageDeletingSelected", { count: names.length }));
    try {
      const response = await safeFetch(`${httpApiBase()}/api/config/endpoints`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names, endpoint_type: "image_endpoints" }),
      });
      const data = await response.json();
      if (data.status !== "ok") throw new Error(data.error || t("llm.imageSaveFailed"));
      setSelectedNames(new Set());
      await reloadEndpoints();
      notifySuccess(t("llm.imageDeletedSelected", { count: Number(data.removed_count || names.length) }));
    } catch (error) {
      notifyError(String((error as Error)?.message || error));
    } finally {
      dismissLoading(busyId);
    }
  }

  return (
    <>
      <div className="card" style={{ marginTop: 10 }}>
        <div className="mb-2 flex items-start justify-between gap-3">
          <div>
            <div className="cardTitle">{t("llm.imageEndpoints")}</div>
            <div className="cardHint">{t("llm.imageEndpointsHint")}</div>
          </div>
          <Button variant="outline" size="sm" className="bg-primary/5 border-primary/30 text-primary hover:bg-primary/10 hover:text-primary" onClick={startAdd} disabled={disabled} title={disabled ? disabledMessage : undefined}>
            <Plus size={15} />{t("llm.addImageEndpoint")}
          </Button>
        </div>

        {endpoints.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-7 text-muted-foreground">
            <ImageIcon size={28} strokeWidth={1.5} className="mb-2 opacity-35" />
            <p className="text-sm">{t("llm.noImageEndpoints")}</p>
          </div>
        ) : (
          <>
          {selectedNames.size > 0 && (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/25 bg-destructive/5 px-3 py-2">
              <div className="text-xs font-medium text-destructive">
                {t("llm.imageSelectedCount", { selected: selectedNames.size, total: endpoints.length })}
              </div>
              <div className="flex items-center gap-2">
                <Button variant="ghost" size="xs" onClick={() => setSelectedNames(new Set())}>
                  {t("llm.imageClearSelection")}
                </Button>
                <Button
                  variant="destructive"
                  size="xs"
                  disabled={disabled}
                  onClick={() => askConfirm(t("llm.imageConfirmBatchDelete", { count: selectedNames.size }), () => void deleteSelected())}
                  title={disabled ? disabledMessage : undefined}
                >
                  <IconTrash size={12} />
                  {t("llm.imageBatchDelete")}
                </Button>
              </div>
            </div>
          )}
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[36px]">
                  <Checkbox
                    checked={endpoints.length > 0 && endpoints.every((endpoint) => selectedNames.has(endpoint.name))}
                    onCheckedChange={(value) => setAllSelected(!!value)}
                    disabled={disabled}
                    aria-label={t("llm.imageSelectAll")}
                  />
                </TableHead>
                <TableHead className="w-[34px]"></TableHead>
                <TableHead>{t("status.endpoint")}</TableHead>
                <TableHead>{t("llm.imageProtocol")}</TableHead>
                <TableHead>{t("status.model")}</TableHead>
                <TableHead className="w-[140px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {endpoints.map((endpoint, index) => (
                <TableRow key={endpoint.name} className={cn(
                  selectedNames.has(endpoint.name) ? "bg-primary/5" : undefined,
                  endpoint.enabled === false ? "opacity-45" : undefined,
                )}>
                  <TableCell className="align-middle">
                    <Checkbox
                      checked={selectedNames.has(endpoint.name)}
                      onCheckedChange={() => toggleSelected(endpoint.name)}
                      disabled={disabled}
                      aria-label={`${t("llm.imageSelectEndpoint")} ${endpoint.name}`}
                    />
                  </TableCell>
                  <TableCell className="align-middle">
                    {(envDraft[endpoint.api_key_env] || "").trim() ? <DotGreen /> : <DotGray />}
                  </TableCell>
                  <TableCell className="font-semibold">
                    <span>{endpoint.name}</span>
                    {index === 0 && endpoint.enabled !== false && <span className="ml-1.5 text-[10px] font-extrabold text-primary">{t("llm.primary")}</span>}
                    {endpoint.enabled === false && <span className="ml-1.5 text-[10px] font-bold text-muted-foreground">{t("llm.disabled")}</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{t(`llm.imageProtocol_${endpoint.api_type}`)}</TableCell>
                  <TableCell className="text-muted-foreground">{endpoint.model}</TableCell>
                  <TableCell>
                    <div className="flex gap-1 justify-end">
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" style={index === 0 ? { visibility: "hidden" } : undefined} onClick={() => moveUp(endpoint.name)} disabled={disabled} title={disabled ? disabledMessage : t("llm.moveUp")}><IconChevronUp size={14} /></Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => void mutate("/api/config/toggle-endpoint", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: endpoint.name, endpoint_type: "image_endpoints" }) })} disabled={disabled} title={disabled ? disabledMessage : endpoint.enabled === false ? t("llm.enable") : t("llm.disable")}>{endpoint.enabled !== false ? <IconPower size={14} /> : <IconCircle size={14} />}</Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => startEdit(endpoint)} disabled={disabled} title={disabled ? disabledMessage : t("llm.edit")}><IconEdit size={14} /></Button>
                      <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} \"${endpoint.name}\"?`, () => void mutate(`/api/config/endpoint/${encodeURIComponent(endpoint.name)}?endpoint_type=image_endpoints`, { method: "DELETE" }))} disabled={disabled} title={disabled ? disabledMessage : t("common.delete")}><IconTrash size={14} /></Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>{originalName ? t("llm.editImageEndpoint") : t("llm.addImageEndpoint")}</DialogTitle>
            <DialogDescription className="sr-only">{t("llm.imageEndpointsHint")}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{t("llm.provider")}</Label>
                <Select value={form.provider in PRESETS ? form.provider : "custom"} onValueChange={applyPreset} disabled={!!originalName}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="dashscope">DashScope</SelectItem>
                    <SelectItem value="openai">OpenAI</SelectItem>
                    <SelectItem value="custom">{t("llm.customProvider")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>{t("llm.imageProtocol")}</Label>
                <Select value={form.apiType} onValueChange={(value) => setForm((current) => ({ ...current, apiType: value as ImageProtocol }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="dashscope">DashScope Native</SelectItem>
                    <SelectItem value="openai_images">OpenAI Images</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5"><Label>{t("llm.endpointName")}</Label><Input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder={`${form.provider || "custom"}-images`} /></div>
            <div className="space-y-1.5"><Label>{t("llm.baseUrl")}</Label><Input value={form.baseUrl} onChange={(event) => setForm((current) => ({ ...current, baseUrl: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label>{t("llm.selectModel")}</Label><Input value={form.model} onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))} placeholder="gpt-image-1" /></div>
            <div className="space-y-1.5"><Label>{t("llm.imageApiKeyLabel")}</Label><Input type="password" value={form.apiKey} onChange={(event) => setForm((current) => ({ ...current, apiKey: event.target.value }))} placeholder={originalName ? t("llm.imageKeepApiKey") : "sk-..."} /></div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1.5"><Label>{t("llm.imageDefaultSize")}</Label><Input value={form.defaultSize} onChange={(event) => setForm((current) => ({ ...current, defaultSize: event.target.value }))} /></div>
              <div className="space-y-1.5"><Label>{t("llm.imageDefaultQuality")}</Label><Input value={form.defaultQuality} onChange={(event) => setForm((current) => ({ ...current, defaultQuality: event.target.value }))} placeholder="high" /></div>
              <div className="space-y-1.5"><Label>{t("llm.advTimeout")}</Label><Input type="number" min={1} value={form.timeout} onChange={(event) => setForm((current) => ({ ...current, timeout: Number(event.target.value) }))} /></div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>{t("common.cancel")}</Button>
            <Button onClick={() => void save()}>{t("common.save")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
