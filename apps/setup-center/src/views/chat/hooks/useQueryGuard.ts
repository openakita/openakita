import { useState, useRef, useCallback } from "react";

/**
 * QueryGuard: 三状态机 + generation 计数器，防止并发查询竞态。
 *
 * 状态流转:
 *   idle  ──startQuery──▸  querying
 *   querying ──endQuery──▸ idle
 *   querying ──startQuery──▸ (abort prev) cancelling ──▸ querying
 *   querying ──cancel──▸ idle
 *
 * 每次 startQuery 递增 generation，回调中通过 isStale(gen) 检测是否过期。
 */

export type QueryState = "idle" | "querying" | "cancelling";

export interface QueryGuardHandle {
  generation: number;
  signal: AbortSignal;
  abort: AbortController;
}

export function useQueryGuard() {
  const [state, setState] = useState<QueryState>("idle");
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const startQuery = useCallback((): QueryGuardHandle => {
    // Abort any existing query
    if (abortRef.current) {
      abortRef.current.abort("superseded");
    }

    generationRef.current += 1;
    const gen = generationRef.current;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState("querying");

    return { generation: gen, signal: ctrl.signal, abort: ctrl };
  }, []);

  const isStale = useCallback((gen: number): boolean => {
    return gen !== generationRef.current;
  }, []);

  const endQuery = useCallback((gen: number) => {
    if (gen === generationRef.current) {
      setState("idle");
      abortRef.current = null;
    }
  }, []);

  const cancel = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort("user_cancelled");
      abortRef.current = null;
    }
    setState("idle");
  }, []);

  return {
    state,
    generation: generationRef,
    startQuery,
    endQuery,
    isStale,
    cancel,
  };
}
