const cache = new Map<string, { dataUrl: string; version: number }>();

function buildKey(agentId: string, version: number): string {
  return `${agentId}__v${version}`;
}

export const AvatarCache = {
  get(agentId: string, version = 0): string | null {
    const entry = cache.get(buildKey(agentId, version));
    return entry?.dataUrl ?? null;
  },

  set(agentId: string, dataUrl: string, version = 0): void {
    cache.set(buildKey(agentId, version), { dataUrl, version });
  },

  invalidate(agentId: string): void {
    for (const key of cache.keys()) {
      if (key.startsWith(`${agentId}__`)) cache.delete(key);
    }
  },

  clear(): void {
    cache.clear();
  },

  size(): number {
    return cache.size;
  },
};
