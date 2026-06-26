import { useState, useRef, useCallback } from "react";

/**
 * QueryGuard: per-conversation 三状态机 + generation 计数器，防止并发查询竞态。
 *
 * 状态流转 (per convId):
 *   idle  ──startQuery──▸  querying
 *   querying ──endQuery──▸ idle
 *   querying ──startQuery──▸ (abort prev) cancelling ──▸ querying
 *   querying ──cancel──▸ idle
 *
 * 每次 startQuery 递增 generation，回调中通过 isStale(gen) 检测是否过期。
 *
 * Uses a Map<convId, slot> so streams for different conversations don't
 * interfere with each other.
 */

export type QueryState = "idle" | "querying" | "cancelling";

export interface QueryGuardHandle {
  generation: number;
  signal: AbortSignal;
  abort: AbortController;
}

interface ConvSlot {
  generation: number;
  abort: AbortController | null;
}

export function useQueryGuard() {
  const [state, setState] = useState<QueryState>("idle");
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const slotsRef = useRef<Map<string, ConvSlot>>(new Map());

  const refreshState = useCallback(() => {
    setState(slotsRef.current.size > 0 || abortRef.current ? "querying" : "idle");
  }, []);

  const startQuery = useCallback((convId?: string): QueryGuardHandle => {
    // Per-conversation slot management
    if (convId) {
      const slot = slotsRef.current.get(convId);
      if (slot?.abort) {
        slot.abort.abort("superseded");
      }
    } else {
      // Fallback: abort global (backward compat for callers without convId)
      if (abortRef.current) {
        abortRef.current.abort("superseded");
      }
    }

    generationRef.current += 1;
    const gen = generationRef.current;
    const ctrl = new AbortController();

    if (convId) {
      slotsRef.current.set(convId, { generation: gen, abort: ctrl });
    } else {
      abortRef.current = ctrl;
    }
    setState("querying");

    return { generation: gen, signal: ctrl.signal, abort: ctrl };
  }, []);

  const isStale = useCallback((gen: number, convId?: string): boolean => {
    if (convId) {
      const slot = slotsRef.current.get(convId);
      return !slot || slot.generation !== gen;
    }
    return gen !== generationRef.current;
  }, []);

  const endQuery = useCallback((gen: number, convId?: string) => {
    if (convId) {
      const slot = slotsRef.current.get(convId);
      if (slot && slot.generation === gen) {
        slotsRef.current.delete(convId);
      }
      refreshState();
      return;
    }
    if (gen === generationRef.current) {
      abortRef.current = null;
    }
    refreshState();
  }, [refreshState]);

  const cancel = useCallback((convId?: string) => {
    if (convId) {
      const slot = slotsRef.current.get(convId);
      if (slot?.abort) {
        slot.abort.abort("user_cancelled");
      }
      slotsRef.current.delete(convId);
      refreshState();
      return;
    }
    if (abortRef.current) {
      abortRef.current.abort("user_cancelled");
      abortRef.current = null;
    }
    for (const slot of slotsRef.current.values()) {
      slot.abort?.abort("user_cancelled");
    }
    slotsRef.current.clear();
    refreshState();
  }, [refreshState]);

  return {
    state,
    generation: generationRef,
    startQuery,
    endQuery,
    isStale,
    cancel,
  };
}
