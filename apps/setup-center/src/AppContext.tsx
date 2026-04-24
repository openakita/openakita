import { createContext, useContext } from "react";
import type { EnvMap } from "./types";

export type AppConfigContextType = {
  envDraft: EnvMap;
  setEnvDraft: (updater: EnvMap | ((prev: EnvMap) => EnvMap)) => void;
  busy: string | null;
  setBusy: (v: string | null) => void;
  error: string | null;
  setError: (v: string | null) => void;
  notice: string | null;
  setNotice: (v: string | null) => void;
  secretShown: Record<string, boolean>;
  setSecretShown: (updater: Record<string, boolean> | ((prev: Record<string, boolean>) => Record<string, boolean>)) => void;
  currentWorkspaceId: string | null;
  serviceStatus: { running: boolean; pid: number | null; pidFile: string } | null;
  shouldUseHttpApi: () => boolean;
  httpApiBase: () => string;
  t: (key: string, vars?: Record<string, unknown>) => string;
  i18n: { language: string; changeLanguage: (lng: string) => Promise<void> };
};

export const AppConfigContext = createContext<AppConfigContextType | null>(null);

export function useAppConfig(): AppConfigContextType {
  const ctx = useContext(AppConfigContext);
  if (!ctx) throw new Error("useAppConfig must be used within AppConfigContext.Provider");
  return ctx;
}

