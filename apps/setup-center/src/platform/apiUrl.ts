// PR-O1: 统一 API URL 拼接 + 自动 encode 路径段。
//
// 之前 MemoryView / SkillView / ScheduleView 等多处都直接 template-literal 拼
//   `${API_BASE}/api/memories/${id}`
// 这里的 `id` 如果含 `/` `?` `#` `%` 之类字符（理论上不会，但旧 sessions.json 偶尔污染过）
// 会破坏路由匹配，甚至导致 ID 被截断 → 删错记录 / 删错任务。改用 apiUrl 后会强制
// 对每段做一次 encodeURIComponent，根治这条路径。
//
// 用法：
//   apiUrl(httpApiBase(), "api", "memories", id)
//     => "<base>/api/memories/<encoded-id>"
//   apiUrl(httpApiBase(), "api", "memories") + "?" + new URLSearchParams(params)
//     => "<base>/api/memories?..."

export type PathSegment = string | number;

export function apiUrl(base: string, ...segments: PathSegment[]): string {
  const baseTrimmed = (base || "").replace(/\/+$/, "");
  const path = segments
    .filter((seg) => seg !== undefined && seg !== null && seg !== "")
    .map((seg) => encodeURIComponent(String(seg)))
    .join("/");
  return path ? `${baseTrimmed}/${path}` : baseTrimmed;
}

// 兼容旧用法：把已有的相对路径（含查询串）安全拼到 base 后面。
// 例如 joinApi(httpApiBase(), "/api/memories?type=fact")
export function joinApi(base: string, relative: string): string {
  if (!relative) return base;
  if (/^https?:\/\//i.test(relative)) return relative;
  const baseTrimmed = (base || "").replace(/\/+$/, "");
  const rel = relative.startsWith("/") ? relative : `/${relative}`;
  return `${baseTrimmed}${rel}`;
}

// 用于把字符串 ID 安全 encode 成可放入路径段的形式。
// 单独导出便于在还没改造的旧代码里"局部加固"：
//   `${API_BASE}/api/memories/${encodePathSegment(id)}`
export function encodePathSegment(value: PathSegment): string {
  return encodeURIComponent(String(value));
}
