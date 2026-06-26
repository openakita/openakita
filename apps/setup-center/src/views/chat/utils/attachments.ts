// ─── 附件 URL 解析（统一入口）───
//
// 历史上 Artifacts.tsx 直接用 `getAssetUrl(art.path) || appendAuthToken(httpUrl)`：
// 在 Tauri 桌面端 `getAssetUrl` 只要 `convertFileSrc` 可用就返回 asset:// URL，
// 但当文件路径不在 Tauri 资产作用域内时该 URL 会在加载时静默失败，且没有任何
// 回退——这就是"重载后图片裂图"的根因之一。后端其实一直提供持久的
// `/api/files?path=` URL（见 api/routes/files.py），它与作用域无关、桌面本地
// 免鉴权、Web 端带 token 即可。
//
// 这里统一解析为 { displayUrl, downloadUrl, fallbackUrl }：
//  - displayUrl：优先 asset 快路径（桌面零拷贝），渲染层用 onError 回退到 fallbackUrl；
//  - downloadUrl / fallbackUrl：始终是持久的 HTTP `/api/files` URL（可下载、可重载）。

import type { ChatArtifact } from "../../../types";
import { appendAuthToken } from "./chatHelpers";
import { getAssetUrl } from "../../../platform";

export type ResolvedAttachment = {
  /** Preferred src for an <img>/<audio> tag (may be an asset:// fast path). */
  displayUrl: string;
  /** Durable HTTP URL for download / "open with" actions. */
  downloadUrl: string;
  /** Durable HTTP URL to swap to if `displayUrl` fails to load. */
  fallbackUrl: string;
};

export function resolveAttachmentUrl(
  art: Pick<ChatArtifact, "file_url" | "path">,
  apiBaseUrl?: string,
): ResolvedAttachment {
  const rawUrl = art.file_url
    ? art.file_url.startsWith("http")
      ? art.file_url
      : `${apiBaseUrl || ""}${art.file_url}`
    : "";
  const httpUrl = rawUrl ? appendAuthToken(rawUrl) : "";
  const assetUrl = getAssetUrl(art.path) || "";
  const displayUrl = assetUrl || httpUrl;
  return { displayUrl, downloadUrl: httpUrl, fallbackUrl: httpUrl };
}
