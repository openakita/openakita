import { useState, useRef, useCallback, useEffect } from "react";
import { logger } from "../../../platform";

export type PermissionMode = "cautious" | "smart" | "trust";

interface SecurityEvent {
  tool: string;
  args: Record<string, unknown>;
  reason: string;
  risk_level: string;
  needs_sandbox: boolean;
  id: string;
}

interface SessionTrustEntry {
  allows: number;
  lastAllowedAt: number;
}

const STORAGE_KEY = "openakita_permissionMode";
const TRUST_ESCALATION_THRESHOLD = 3;

function shouldAutoAllow(mode: PermissionMode, riskLevel: string, sessionTrust: Map<string, SessionTrustEntry>): boolean {
  const rl = riskLevel.toLowerCase();

  if (mode === "trust") {
    if (rl === "low" || rl === "medium") return true;
  } else if (mode === "smart") {
    if (rl === "low") return true;
    const entry = sessionTrust.get("*");
    if (entry && entry.allows >= TRUST_ESCALATION_THRESHOLD && rl === "medium") return true;
  }

  return false;
}

export function useSecurityPolicy() {
  const [permissionMode, setPermissionMode] = useState<PermissionMode>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "cautious" || stored === "smart" || stored === "trust") return stored;
    } catch {}
    return "smart";
  });

  const sessionTrustRef = useRef(new Map<string, SessionTrustEntry>());

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, permissionMode); } catch {}
  }, [permissionMode]);

  const recordAllow = useCallback((toolName: string) => {
    const map = sessionTrustRef.current;
    const toolEntry = map.get(toolName) || { allows: 0, lastAllowedAt: 0 };
    toolEntry.allows += 1;
    toolEntry.lastAllowedAt = Date.now();
    map.set(toolName, toolEntry);

    const globalEntry = map.get("*") || { allows: 0, lastAllowedAt: 0 };
    globalEntry.allows += 1;
    globalEntry.lastAllowedAt = Date.now();
    map.set("*", globalEntry);
  }, []);

  const recordDeny = useCallback((_toolName: string) => {
    sessionTrustRef.current.set("*", { allows: 0, lastAllowedAt: 0 });
  }, []);

  const checkAutoAllow = useCallback(
    (event: SecurityEvent): boolean => {
      return shouldAutoAllow(permissionMode, event.risk_level, sessionTrustRef.current);
    },
    [permissionMode],
  );

  const getSessionTrustInfo = useCallback((toolName: string) => {
    const entry = sessionTrustRef.current.get(toolName);
    const globalEntry = sessionTrustRef.current.get("*");
    return {
      toolAllows: entry?.allows ?? 0,
      globalAllows: globalEntry?.allows ?? 0,
      isEscalated: (globalEntry?.allows ?? 0) >= TRUST_ESCALATION_THRESHOLD,
    };
  }, []);

  const resetSessionTrust = useCallback(() => {
    sessionTrustRef.current.clear();
  }, []);

  return {
    permissionMode,
    setPermissionMode,
    checkAutoAllow,
    recordAllow,
    recordDeny,
    getSessionTrustInfo,
    resetSessionTrust,
  };
}
