import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Row = { id: string; key: string; value: string };

type EnvDictEditorProps = {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  helperText?: string;
  maxRows?: number;
  disabled?: boolean;
};

const MAX_ROWS_DEFAULT = 50;

function rowsToRecord(rows: Row[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    out[key] = row.value;
  }
  return out;
}

function recordToRows(record: Record<string, string>): Row[] {
  return Object.entries(record).map(([key, value], i) => ({
    id: `row-init-${i}-${key}`,
    key,
    value,
  }));
}

let rowIdCounter = 0;
const nextRowId = () => `row-${Date.now()}-${rowIdCounter++}`;

export function EnvDictEditor({
  value,
  onChange,
  helperText,
  maxRows = MAX_ROWS_DEFAULT,
  disabled = false,
}: EnvDictEditorProps) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Row[]>(() => recordToRows(value));

  const lastUpstreamRef = useRef<string>(JSON.stringify(value));
  useEffect(() => {
    const serialized = JSON.stringify(value);
    if (serialized !== lastUpstreamRef.current) {
      lastUpstreamRef.current = serialized;
      setRows(recordToRows(value));
    }
  }, [value]);

  const commit = (next: Row[]) => {
    setRows(next);
    const record = rowsToRecord(next);
    lastUpstreamRef.current = JSON.stringify(record);
    onChange(record);
  };

  const addRow = () => {
    if (rows.length >= maxRows) return;
    commit([...rows, { id: nextRowId(), key: "", value: "" }]);
  };

  const removeRow = (id: string) => {
    commit(rows.filter((r) => r.id !== id));
  };

  const updateRow = (id: string, patch: Partial<Row>) => {
    commit(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  };

  return (
    <div className="space-y-2 rounded-lg border p-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold tracking-wide uppercase text-muted-foreground">
          {t("agentManager.cliEnvTitle")}
        </p>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          onClick={addRow}
          disabled={disabled || rows.length >= maxRows}
          type="button"
        >
          + {t("agentManager.cliEnvAdd")}
        </Button>
      </div>
      {helperText && (
        <p className="text-[11px] text-muted-foreground">{helperText}</p>
      )}
      <div className="space-y-1.5">
        {rows.map((row) => (
          <div key={row.id} className="flex items-center gap-1.5">
            <Input
              className="h-8 flex-1 font-mono text-xs"
              value={row.key}
              placeholder={t("agentManager.cliEnvKey")}
              disabled={disabled}
              onChange={(e) => updateRow(row.id, { key: e.target.value })}
            />
            <span className="text-xs text-muted-foreground">=</span>
            <Input
              className="h-8 flex-[2] font-mono text-xs"
              value={row.value}
              placeholder={t("agentManager.cliEnvValue")}
              disabled={disabled}
              onChange={(e) => updateRow(row.id, { value: e.target.value })}
            />
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={() => removeRow(row.id)}
              disabled={disabled}
              type="button"
              aria-label={t("agentManager.cliEnvRemove")}
            >
              ×
            </Button>
          </div>
        ))}
        {rows.length === 0 && (
          <p className="py-2 text-[11px] italic text-muted-foreground">
            {t("agentManager.cliEnvEmpty")}
          </p>
        )}
      </div>
    </div>
  );
}
