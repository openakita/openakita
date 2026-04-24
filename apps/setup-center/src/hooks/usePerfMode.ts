import { useState, useEffect, useCallback } from "react";

export type PerfMode = "normal" | "low";

const STORAGE_KEY = "openakita_perf_mode";

function readPerfMode(): PerfMode {
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "low" ? "low" : "normal";
}

function applyPerfMode(mode: PerfMode) {
  if (mode === "low") {
    document.documentElement.dataset.perfMode = "low";
  } else {
    delete document.documentElement.dataset.perfMode;
  }
}

export function usePerfMode() {
  const [mode, setMode] = useState<PerfMode>(readPerfMode);

  useEffect(() => {
    applyPerfMode(mode);
  }, [mode]);

  const toggle = useCallback(() => {
    setMode((prev) => {
      const next = prev === "normal" ? "low" : "normal";
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  const set = useCallback((m: PerfMode) => {
    localStorage.setItem(STORAGE_KEY, m);
    setMode(m);
  }, []);

  return { mode, toggle, set } as const;
}

