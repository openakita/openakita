// Multi-server connection manager for Capacitor mobile app.
// Stores server list in localStorage; each entry tracks name, url, timestamps.

export interface ServerEntry {
  id: string;
  name: string;
  url: string;
  addedAt: number;
  lastConnectedAt?: number;
}

const STORAGE_KEY = "openakita_servers";
const ACTIVE_KEY = "openakita_active_server";

function readList(): ServerEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function writeList(list: ServerEntry[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

export function getServers(): ServerEntry[] {
  return readList();
}

export function getActiveServerId(): string | null {
  return localStorage.getItem(ACTIVE_KEY);
}

export function getActiveServer(): ServerEntry | null {
  const id = getActiveServerId();
  if (!id) return null;
  return readList().find((s) => s.id === id) ?? null;
}

export function addServer(name: string, url: string): ServerEntry {
  const list = readList();
  const entry: ServerEntry = {
    id: crypto.randomUUID(),
    name: name || url,
    url: normalizeUrl(url),
    addedAt: Date.now(),
  };
  list.push(entry);
  writeList(list);
  localStorage.setItem(ACTIVE_KEY, entry.id);
  return entry;
}

export function updateServer(
  id: string,
  patch: Partial<Pick<ServerEntry, "name" | "url">>,
): void {
  const list = readList();
  const idx = list.findIndex((s) => s.id === id);
  if (idx < 0) return;
  if (patch.name !== undefined) list[idx].name = patch.name;
  if (patch.url !== undefined) list[idx].url = normalizeUrl(patch.url);
  writeList(list);
}

export function removeServer(id: string): void {
  let list = readList();
  list = list.filter((s) => s.id !== id);
  writeList(list);
  if (getActiveServerId() === id) {
    if (list.length > 0) {
      localStorage.setItem(ACTIVE_KEY, list[0].id);
    } else {
      localStorage.removeItem(ACTIVE_KEY);
    }
  }
}

export function setActiveServer(id: string): void {
  const list = readList();
  const entry = list.find((s) => s.id === id);
  if (!entry) return;
  entry.lastConnectedAt = Date.now();
  writeList(list);
  localStorage.setItem(ACTIVE_KEY, id);
}

export async function testConnection(
  url: string,
): Promise<{ ok: boolean; name?: string; version?: string; error?: string }> {
  const normalized = normalizeUrl(url);
  try {
    const res = await fetch(`${normalized}/api/health`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    const data = await res.json();
    return { ok: true, name: data.app_name, version: data.version };
  } catch (e: any) {
    const msg = e?.message || String(e);
    if (msg.includes("abort") || msg.includes("timeout")) {
      return { ok: false, error: "连接超时" };
    }
    return { ok: false, error: "无法连接服务器" };
  }
}

function normalizeUrl(url: string): string {
  let u = url.trim();
  if (!u) return u;
  if (!/^https?:\/\//i.test(u)) u = `http://${u}`;
  return u.replace(/\/+$/, "");
}
