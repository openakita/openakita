import { useRef, useCallback } from "react";

export type BreakerState = "closed" | "open" | "half-open";

interface CircuitBreakerConfig {
  failureThreshold?: number;
  cooldownMs?: number;
  halfOpenMaxAttempts?: number;
}

const DEFAULTS = {
  failureThreshold: 5,
  cooldownMs: 30_000,
  halfOpenMaxAttempts: 2,
};

export function useCircuitBreaker(config?: CircuitBreakerConfig) {
  const failureThreshold = config?.failureThreshold ?? DEFAULTS.failureThreshold;
  const cooldownMs = config?.cooldownMs ?? DEFAULTS.cooldownMs;
  const halfOpenMax = config?.halfOpenMaxAttempts ?? DEFAULTS.halfOpenMaxAttempts;

  const stateRef = useRef<BreakerState>("closed");
  const failCountRef = useRef(0);
  const openedAtRef = useRef(0);
  const halfOpenAttemptsRef = useRef(0);

  const getState = useCallback((): BreakerState => {
    if (stateRef.current === "open") {
      if (Date.now() - openedAtRef.current >= cooldownMs) {
        stateRef.current = "half-open";
        halfOpenAttemptsRef.current = 0;
      }
    }
    return stateRef.current;
  }, [cooldownMs]);

  const recordFailure = useCallback((): BreakerState => {
    const state = getState();
    if (state === "half-open") {
      halfOpenAttemptsRef.current += 1;
      if (halfOpenAttemptsRef.current >= halfOpenMax) {
        stateRef.current = "open";
        openedAtRef.current = Date.now();
      }
    } else {
      failCountRef.current += 1;
      if (failCountRef.current >= failureThreshold) {
        stateRef.current = "open";
        openedAtRef.current = Date.now();
        failCountRef.current = 0;
      }
    }
    return stateRef.current;
  }, [getState, failureThreshold, halfOpenMax]);

  const recordSuccess = useCallback(() => {
    stateRef.current = "closed";
    failCountRef.current = 0;
    halfOpenAttemptsRef.current = 0;
  }, []);

  const reset = useCallback(() => {
    stateRef.current = "closed";
    failCountRef.current = 0;
    openedAtRef.current = 0;
    halfOpenAttemptsRef.current = 0;
  }, []);

  const canAttempt = useCallback((): boolean => {
    const s = getState();
    return s === "closed" || s === "half-open";
  }, [getState]);

  return {
    getState,
    recordFailure,
    recordSuccess,
    reset,
    canAttempt,
  };
}
