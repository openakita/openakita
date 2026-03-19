import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_WEB, IS_TAURI } from "../platform";
import { safeFetch } from "../providers";
import { IconInfo } from "../icons";
import type { EnvMap } from "../types";
import { envGet, envSet } from "../utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { IconRefresh } from "../icons";

type EnvFieldProps = {
  envDraft: EnvMap;
  onEnvChange: (updater: (prev: EnvMap) => EnvMap) => void;
  busy?: string | null;
};

function FieldLabel({ label, help, envKey, htmlFor }: {
  label: string; help?: string; envKey?: string; htmlFor?: string;
}) {
  const hasTooltip = !!(help || envKey);
  return (
    <Label htmlFor={htmlFor} className="text-sm font-medium">
      {label}
      {hasTooltip && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="ml-1 text-muted-foreground/50 cursor-help align-middle inline-flex">
              <IconInfo size={13} />
            </span>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">
            {help && <p>{help}</p>}
            {envKey && <p className="font-mono text-[11px] opacity-70">{envKey}</p>}
          </TooltipContent>
        </Tooltip>
      )}
    </Label>
  );
}

export function FieldText({
  k, label, placeholder, help, type,
  envDraft, onEnvChange,
}: EnvFieldProps & {
  k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password";
}) {
  return (
    <div className="space-y-1.5">
      <FieldLabel label={label} help={help} envKey={k} />
      <Input
        value={envGet(envDraft, k)}
        onChange={(e) => onEnvChange((m) => envSet(m, k, e.target.value))}
        placeholder={placeholder}
        type={type || "text"}
      />
    </div>
  );
}

export function FieldBool({
  k, label, help, defaultValue,
  envDraft, onEnvChange,
}: EnvFieldProps & {
  k: string; label: string; help?: string; defaultValue?: boolean;
}) {
  const v = envGet(envDraft, k, defaultValue ? "true" : "false").toLowerCase() === "true";
  const fieldId = `field-bool-${k}`;
  return (
    <div className="space-y-1.5">
      <FieldLabel label={label} help={help} envKey={k} htmlFor={fieldId} />
      <label
        htmlFor={fieldId}
        className={cn(
          "inline-flex items-center gap-2.5 h-9 px-3 rounded-md border cursor-pointer select-none transition-colors",
          v ? "border-primary/30 bg-primary/5" : "border-input bg-transparent"
        )}
      >
        <Switch
          id={fieldId}
          checked={v}
          onCheckedChange={(checked) =>
            onEnvChange((m) => envSet(m, k, String(!!checked)))
          }
        />
        <span className={cn("text-sm w-8", v ? "text-foreground" : "text-muted-foreground")}>
          {v ? "ON" : "OFF"}
        </span>
      </label>
    </div>
  );
}

export function FieldSelect({
  k, label, options, help,
  envDraft, onEnvChange,
}: EnvFieldProps & {
  k: string; label: string; options: { value: string; label: string }[]; help?: string;
}) {
  const raw = envGet(envDraft, k);
  const value = options.some((o) => o.value === raw) ? raw : (options[0]?.value ?? "");
  return (
    <div className="space-y-1.5">
      <FieldLabel label={label} help={help} envKey={k} />
      <Select
        value={value}
        onValueChange={(v) => onEnvChange((m) => envSet(m, k, v))}
      >
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function FieldCombo({
  k, label, options, placeholder, help,
  envDraft, onEnvChange,
}: EnvFieldProps & {
  k: string; label: string; options: { value: string; label: string }[]; placeholder?: string; help?: string;
}) {
  const { t } = useTranslation();
  const currentVal = envGet(envDraft, k);
  const isPreset = options.some((o) => o.value === currentVal);
  return (
    <div className="space-y-1.5">
      <FieldLabel label={label} help={help} envKey={k} />
      <div className="flex gap-1.5">
        <Select
          value={isPreset ? currentVal : "__custom__"}
          onValueChange={(v) => {
            if (v !== "__custom__") {
              onEnvChange((m) => envSet(m, k, v));
            }
          }}
        >
          <SelectTrigger className="shrink-0 min-w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
            ))}
            <SelectItem value="__custom__">{t("common.custom") || "自定义..."}</SelectItem>
          </SelectContent>
        </Select>
        {(!isPreset || currentVal === "") && (
          <Input
            className="flex-1"
            value={currentVal}
            onChange={(e) => onEnvChange((m) => envSet(m, k, e.target.value))}
            placeholder={placeholder || t("common.custom") || "自定义输入..."}
          />
        )}
      </div>
    </div>
  );
}

export function FieldSlider({
  k, label, help, min, max, step, defaultValue, unit,
  envDraft, onEnvChange,
}: EnvFieldProps & {
  k: string; label: string; help?: string; unit?: string;
  min: number; max: number; step: number; defaultValue: number;
}) {
  const raw = envGet(envDraft, k, String(defaultValue));
  const num = Number(raw) || defaultValue;
  const clamped = Math.min(Math.max(num, min), max);

  const handleSlider = (vals: number[]) => {
    onEnvChange((m) => envSet(m, k, String(vals[0])));
  };

  const handleInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    onEnvChange((m) => envSet(m, k, v));
  };

  const handleBlur = () => {
    const v = Math.min(Math.max(Number(raw) || defaultValue, min), max);
    const rounded = Math.round(v / step) * step;
    onEnvChange((m) => envSet(m, k, String(rounded)));
  };

  return (
    <div className="space-y-1.5">
      <FieldLabel label={label} help={help} envKey={k} />
      <div className="flex items-center gap-3">
        <div className="flex-1 flex flex-col">
          <Slider
            min={min}
            max={max}
            step={step}
            value={[clamped]}
            onValueChange={handleSlider}
          />
          <div className="flex justify-between text-[11px] text-muted-foreground mt-1 px-0.5">
            <span>{min}</span>
            <span>{max}</span>
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-1">
          <Input
            className="w-20 text-center tabular-nums text-xs"
            value={raw}
            onChange={handleInput}
            onBlur={handleBlur}
            type="text"
            inputMode="decimal"
          />
          {unit && <span className="text-[11px] text-muted-foreground whitespace-nowrap">{unit}</span>}
        </div>
      </div>
    </div>
  );
}

export function TelegramPairingCodeHint({
  currentWorkspaceId, apiBase, envDraft, onEnvChange,
}: {
  currentWorkspaceId: string | null;
  apiBase?: string;
  envDraft?: EnvMap;
  onEnvChange?: (updater: (prev: EnvMap) => EnvMap) => void;
}) {
  const { t } = useTranslation();
  const [currentCode, setCurrentCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const syncToEnv = useCallback((code: string) => {
    if (!onEnvChange || !envDraft) return;
    const existing = envGet(envDraft, "TELEGRAM_PAIRING_CODE", "");
    if (!existing) {
      onEnvChange((m) => envSet(m, "TELEGRAM_PAIRING_CODE", code));
    }
  }, [onEnvChange, envDraft]);

  const loadCode = useCallback(async () => {
    setLoading(true);
    try {
      let code: string | null = null;
      if (IS_TAURI && currentWorkspaceId) {
        const raw = await invoke<string>("workspace_read_file", {
          workspaceId: currentWorkspaceId,
          relativePath: "data/telegram/pairing/pairing_code.txt",
        });
        code = raw.trim() || null;
      } else {
        const base = apiBase || "";
        const res = await safeFetch(`${base}/api/im/telegram/pairing-code`);
        const data = await res.json();
        code = data.code || null;
      }
      setCurrentCode(code);
      if (code) syncToEnv(code);
    } catch {
      setCurrentCode(null);
    } finally {
      setLoading(false);
    }
  }, [currentWorkspaceId, apiBase, syncToEnv]);

  useEffect(() => { loadCode(); }, [loadCode]);

  return (
    <div className="flex items-center gap-1.5 flex-wrap text-xs text-muted-foreground mt-1 leading-7">
      <span>🔑 {t("config.imCurrentPairingCode")}：</span>
      {loading ? (
        <span className="opacity-50">...</span>
      ) : currentCode ? (
        <code className="bg-muted px-2 py-0.5 rounded text-[13px] font-semibold tracking-widest select-all">{currentCode}</code>
      ) : (
        <span className="opacity-50">{t("config.imPairingCodeNotGenerated")}</span>
      )}
      <Button variant="outline" size="sm" className="h-6 px-2 text-[11px] gap-1" onClick={loadCode} disabled={loading}>
        <IconRefresh size={12} /> {t("common.refresh")}
      </Button>
    </div>
  );
}
