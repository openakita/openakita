/**
 * Thin API client layer over safeFetch.
 *
 * Eliminates repeated `{ method: "POST", headers: {...}, body: JSON.stringify() }`
 * boilerplate across 50+ call sites.  Functions accept a full URL (callers compose
 * it from httpApiBase() or equivalent) so there's zero coupling to component state.
 */

import { safeFetch } from "./providers";

export async function apiGet<T = unknown>(
  url: string,
  opts?: { timeout?: number; signal?: AbortSignal },
): Promise<T> {
  const signal =
    opts?.signal ?? (opts?.timeout ? AbortSignal.timeout(opts.timeout) : undefined);
  const res = await safeFetch(url, { signal });
  return res.json() as Promise<T>;
}

export async function apiPost<T = unknown>(
  url: string,
  body?: unknown,
  opts?: { timeout?: number; signal?: AbortSignal },
): Promise<T> {
  const signal =
    opts?.signal ?? (opts?.timeout ? AbortSignal.timeout(opts.timeout) : undefined);
  const res = await safeFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body != null ? JSON.stringify(body) : undefined,
    signal,
  });
  return res.json() as Promise<T>;
}

export async function apiPostRaw(
  url: string,
  body?: unknown,
  opts?: { timeout?: number; signal?: AbortSignal },
): Promise<Response> {
  const signal =
    opts?.signal ?? (opts?.timeout ? AbortSignal.timeout(opts.timeout) : undefined);
  return safeFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body != null ? JSON.stringify(body) : undefined,
    signal,
  });
}
