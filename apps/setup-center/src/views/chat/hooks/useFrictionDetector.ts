import { useRef, useCallback } from "react";

interface FrictionState {
  consecutiveErrors: number;
  lastErrorAt: number;
  idleSince: number;
  retriesOnSameError: number;
  lastErrorCategory: string;
}

const FRICTION_THRESHOLD_ERRORS = 3;
const FRICTION_THRESHOLD_IDLE_MS = 5 * 60 * 1000;

export function useFrictionDetector(onFrictionDetected: (hint: string) => void) {
  const stateRef = useRef<FrictionState>({
    consecutiveErrors: 0,
    lastErrorAt: 0,
    idleSince: Date.now(),
    retriesOnSameError: 0,
    lastErrorCategory: "",
  });

  const onFrictionRef = useRef(onFrictionDetected);
  onFrictionRef.current = onFrictionDetected;

  const recordError = useCallback((category: string) => {
    const s = stateRef.current;
    s.consecutiveErrors += 1;
    s.lastErrorAt = Date.now();

    if (category === s.lastErrorCategory) {
      s.retriesOnSameError += 1;
    } else {
      s.retriesOnSameError = 1;
      s.lastErrorCategory = category;
    }

    if (s.consecutiveErrors >= FRICTION_THRESHOLD_ERRORS) {
      onFrictionRef.current("repeated_errors");
      s.consecutiveErrors = 0;
    } else if (s.retriesOnSameError >= 3) {
      onFrictionRef.current("same_error_retry");
      s.retriesOnSameError = 0;
    }
  }, []);

  const recordSuccess = useCallback(() => {
    const s = stateRef.current;
    s.consecutiveErrors = 0;
    s.retriesOnSameError = 0;
    s.lastErrorCategory = "";
    s.idleSince = Date.now();
  }, []);

  const recordActivity = useCallback(() => {
    stateRef.current.idleSince = Date.now();
  }, []);

  const checkIdle = useCallback(() => {
    const elapsed = Date.now() - stateRef.current.idleSince;
    if (elapsed >= FRICTION_THRESHOLD_IDLE_MS && stateRef.current.consecutiveErrors > 0) {
      onFrictionRef.current("idle_after_error");
    }
  }, []);

  return {
    recordError,
    recordSuccess,
    recordActivity,
    checkIdle,
  };
}
