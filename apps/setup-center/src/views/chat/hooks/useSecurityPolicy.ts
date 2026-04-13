import { useState, useRef, useCallback, useEffect } from "react";
import { logger } from "../../../platform";

export type PermissionMode = "cautious" | "smart" | "yolo";

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

const TRUST_ESCALATION_THRESHOLD = 3;

function shouldAutoAllow(
  mode: PermissionMode,
  riskLevel: string,
  sessionTrust: Map<string, SessionTrustEntry>,
): boolean {
  const rl = riskLevel.toLowerCase();

  if (mode === "yolo") {
    if (rl === "low" || rl === "medium" || rl === "high") return true;
  } else if (mode === "smart") {
    if (rl === "low") return true;
    const entry = sessionTrust.get("*");
    if (entry && entry.allows >= TRUST_ESCALATION_THRESHOLD && rl === "medium")
      return true;
  }

  return false;
}

export function useSecurityPolicy(apiBase: string) {
  const [permissionMode, setPermissionModeLocal] = useState<PermissionMode>("smart");
  const sessionTrustRef = useRef(new Map<string, SessionTrustEntry>());

  const fetchMode = useCallback(() => {
    fetch(`${apiBase}/api/config/permission-mode`)
      .then((r) => r.json())
      .then((d) => {
        const m = d.mode === "trust" ? "yolo" : d.mode;
        if (m === "cautious" || m === "smart" || m === "yolo") {
          setPermissionModeLocal(m);
        }
      })
      .catch((e) => logger.warn?.("[useSecurityPolicy] fetch mode failed", e));
  }, [apiBase]);

  useEffect(() => {
    fetchMode();
    const onVisible = () => { if (document.visibilityState === "visible") fetchMode(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [fetchMode]);

  const setPermissionMode = useCallback(
    (mode: PermissionMode) => {
      setPermissionModeLocal(mode);
      fetch(`${apiBase}/api/config/permission-mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      }).catch(() => {});
    },
    [apiBase],
  );

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
      return shouldAutoAllow(
        permissionMode,
        event.risk_level,
        sessionTrustRef.current,
      );
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
