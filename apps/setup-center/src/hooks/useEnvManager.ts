/**
 * Encapsulates all env-draft state and persistence logic that was previously
 * scattered across App.tsx (envDraft, secretShown, ensureEnvLoaded, saveEnvKeys).
 *
 * Dependencies are injected via `opts` so the hook stays decoupled from service
 * status, workspace routing, etc.
 */

import { useRef, useState } from "react";
import { invoke, IS_TAURI, logger } from "../platform";
import { safeFetch } from "../providers";
import { parseEnv } from "../utils";
import type { EnvMap } from "../types";

export interface UseEnvManagerOpts {
  currentWorkspaceId: string | null;
  shouldUseHttpApi: () => boolean;
  httpApiBase: () => string;
}

const ENV_DEFAULTS: Record<string, string> = {
  DESKTOP_ENABLED: "true",
  MCP_ENABLED: "true",
};

export function useEnvManager(opts: UseEnvManagerOpts) {
  const [envDraft, setEnvDraft] = useState<EnvMap>({});
  const [secretShown, setSecretShown] = useState<Record<string, boolean>>({});
  const envLoadedForWs = useRef<string | null>(null);

  const optsRef = useRef(opts);
  optsRef.current = opts;

  async function ensureEnvLoaded(workspaceId: string): Promise<EnvMap> {
    if (envLoadedForWs.current === workspaceId) return envDraft;
    let parsed: EnvMap = {};
    const { shouldUseHttpApi, httpApiBase } = optsRef.current;

    if (shouldUseHttpApi()) {
      try {
        const res = await safeFetch(`${httpApiBase()}/api/config/env`);
        const data = await res.json();
        parsed = data.env || {};
      } catch {
        if (IS_TAURI && workspaceId) {
          try {
            const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
            parsed = parseEnv(content);
          } catch { parsed = {}; }
        }
      }
    } else if (IS_TAURI && workspaceId) {
      try {
        const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
        parsed = parseEnv(content);
      } catch { parsed = {}; }
    }

    for (const [dk, dv] of Object.entries(ENV_DEFAULTS)) {
      if (!(dk in parsed)) parsed[dk] = dv;
    }
    setEnvDraft(parsed);
    envLoadedForWs.current = workspaceId;
    return parsed;
  }

  async function saveEnvKeys(keys: string[]): Promise<{ restartRequired?: boolean; hotReloadable?: boolean }> {
    const { shouldUseHttpApi, httpApiBase, currentWorkspaceId } = optsRef.current;

    const entries: Record<string, string> = {};
    const deleteKeys: string[] = [];
    for (const k of keys) {
      if (Object.prototype.hasOwnProperty.call(envDraft, k)) {
        const v = (envDraft[k] ?? "").trim();
        if (v.length > 0) {
          entries[k] = v;
        } else {
          deleteKeys.push(k);
        }
      }
    }
    if (!Object.keys(entries).length && !deleteKeys.length) return {};

    if (shouldUseHttpApi()) {
      try {
        const res = await safeFetch(`${httpApiBase()}/api/config/env`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entries, delete_keys: deleteKeys }),
        });
        const data = await res.json().catch(() => ({}));
        return {
          restartRequired: data.restart_required ?? false,
          hotReloadable: data.hot_reloadable ?? true,
        };
      } catch {
        logger.warn("useEnvManager", "saveEnvKeys: HTTP failed, falling back to Tauri");
      }
    }
    if (IS_TAURI && currentWorkspaceId) {
      await ensureEnvLoaded(currentWorkspaceId);
      const tauriEntries = [
        ...Object.entries(entries).map(([key, value]) => ({ key, value })),
        ...deleteKeys.map((key) => ({ key, value: "" })),
      ];
      await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries: tauriEntries });
    }
    return {};
  }

  function resetEnvLoaded() {
    envLoadedForWs.current = null;
    setEnvDraft({});
  }

  function markEnvLoaded(workspaceId: string) {
    envLoadedForWs.current = workspaceId;
  }

  return {
    envDraft,
    setEnvDraft,
    secretShown,
    setSecretShown,
    ensureEnvLoaded,
    saveEnvKeys,
    resetEnvLoaded,
    markEnvLoaded,
  };
}
