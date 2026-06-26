import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ChatArtifact } from "../utils/chatTypes";
import { resolveAttachmentUrl } from "../utils/attachments";
import { downloadFile, openFileWithDefault, showInFolder, logger } from "../../../platform";
import { IconDownload, getFileTypeIcon } from "../../../icons";

let _artifactClickTimer: ReturnType<typeof setTimeout> | null = null;

/** Append a cache-busting param so the browser re-requests instead of replaying a cached 401. */
function withCacheBust(url: string): string {
  return `${url}${url.includes("?") ? "&" : "?"}_r=${Date.now()}`;
}

function VoiceArtifact({ displayUrl, fallbackUrl, caption }: { displayUrl: string; fallbackUrl: string; caption?: string }) {
  const { t } = useTranslation();
  const [src, setSrc] = useState(displayUrl);
  const [error, setError] = useState(false);
  return (
    <div style={{ marginBottom: 8 }}>
      <audio
        controls
        preload="metadata"
        src={src}
        style={{ maxWidth: "100%" }}
        onError={() => {
          // asset:// (or stale-token) source failed — try the durable HTTP URL
          // with a freshly read auth token once before surfacing an error.
          if (fallbackUrl && src !== fallbackUrl) setSrc(fallbackUrl);
          else setError(true);
        }}
      />
      {error && (
        <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 4 }}>
          {t("chat.audioLoadFailed", "音频加载失败，请检查文件是否存在或格式是否支持")}
        </div>
      )}
      {caption && (
        <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>{caption}</div>
      )}
    </div>
  );
}

/** Read-only status card shown when an image attachment cannot be loaded at all. */
function BrokenImageCard({ art, downloadUrl }: { art: ChatArtifact; downloadUrl: string }) {
  const { t } = useTranslation();
  const FileIcon = getFileTypeIcon(art.name || "image.png");
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 10, maxWidth: "100%",
      padding: "10px 14px", borderRadius: 10, marginBottom: 8,
      border: "1px dashed var(--line)", background: "var(--panel)",
    }}>
      <FileIcon size={26} style={{ opacity: 0.5, flexShrink: 0 }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {art.name || t("chat.image", "图片")}
        </span>
        <span style={{ fontSize: 11, color: "var(--danger)", opacity: 0.85 }}>
          {t("chat.imageUnavailable", "图片不可用")}
        </span>
      </div>
      {downloadUrl && (
        <button
          title={t("chat.openFile", "打开文件")}
          style={{
            marginLeft: 4, flexShrink: 0, border: "1px solid var(--line)",
            background: "transparent", borderRadius: 6, padding: "4px 8px",
            cursor: "pointer", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4,
          }}
          onClick={async () => {
            try {
              const savedPath = await downloadFile(downloadUrl, art.name || `image-${Date.now()}.png`);
              await openFileWithDefault(savedPath);
            } catch (err) {
              logger.error("Chat", "图片打开失败", { error: String(err) });
            }
          }}
        >
          <IconDownload size={13} />
          {t("chat.openFile", "打开文件")}
        </button>
      )}
    </div>
  );
}

export function ArtifactItem({ art, apiBaseUrl, onImagePreview }: {
  art: ChatArtifact;
  apiBaseUrl?: string;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
}) {
  const { t } = useTranslation();
  const { displayUrl, downloadUrl } = resolveAttachmentUrl(art, apiBaseUrl);
  // Image load recovery ladder, from the fast asset:// path down to a giving-up
  // placeholder card:
  //   0 initial (asset:// or http) → 1 durable /api/files HTTP with a freshly
  //   read token → 2 same HTTP + cache-buster (defeats a cached 401 / expired
  //   token) → 3 failed (show BrokenImageCard instead of a broken <img>).
  const [imgSrc, setImgSrc] = useState(displayUrl);
  const [imgStage, setImgStage] = useState(0);

  const handleImgError = useCallback(() => {
    if (imgStage >= 2) { setImgStage(3); return; }
    const http = resolveAttachmentUrl(art, apiBaseUrl).fallbackUrl;
    if (!http) { setImgStage(3); return; }
    if (imgStage === 0 && http !== imgSrc) {
      setImgSrc(http);
      setImgStage(1);
      return;
    }
    // Already on HTTP (or asset path equalled HTTP): retry once with a fresh
    // token + cache-buster before giving up.
    setImgSrc(withCacheBust(http));
    setImgStage(2);
  }, [imgStage, imgSrc, art, apiBaseUrl]);

  if (art.artifact_type === "image") {
    if (imgStage >= 3) {
      return <BrokenImageCard art={art} downloadUrl={downloadUrl} />;
    }
    return (
      <div style={{ marginBottom: 8, position: "relative", display: "inline-block" }}>
        <img
          src={imgSrc}
          alt={art.caption || art.name}
          onError={handleImgError}
          style={{
            maxWidth: "100%",
            maxHeight: 400,
            borderRadius: 8,
            border: "1px solid var(--line)",
            display: "block",
            cursor: "pointer",
          }}
          onClick={() => {
            if (_artifactClickTimer) clearTimeout(_artifactClickTimer);
            _artifactClickTimer = setTimeout(() => {
              // Use the currently-displayed src (which may have fallen back from a
              // dead asset:// URL to the durable HTTP one) so the preview modal
              // shows the same working image instead of re-opening the broken URL.
              onImagePreview?.(imgSrc, downloadUrl, art.name || "image");
            }, 250);
          }}
          onDoubleClick={() => {
            if (_artifactClickTimer) { clearTimeout(_artifactClickTimer); _artifactClickTimer = null; }
            (async () => {
              try {
                const savedPath = await downloadFile(downloadUrl, art.name || `image-${Date.now()}.png`);
                await openFileWithDefault(savedPath);
              } catch (err) {
                logger.error("Chat", "图片打开失败", { error: String(err) });
              }
            })();
          }}
        />
        <button
          title={t("chat.downloadImage") || "保存图片"}
          style={{
            position: "absolute", top: 8, right: 8,
            background: "rgba(0,0,0,0.55)", color: "#fff",
            border: "none", borderRadius: 6, width: 32, height: 32,
            display: "flex", alignItems: "center", justifyContent: "center",
            cursor: "pointer", opacity: 0.8, transition: "opacity 0.15s",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.8"; }}
          onClick={async (e) => {
            e.stopPropagation();
            try {
              const savedPath = await downloadFile(downloadUrl, art.name || `image-${Date.now()}.png`);
              await showInFolder(savedPath);
            } catch (err) {
              logger.error("Chat", "图片下载失败", { error: String(err) });
            }
          }}
        >
          <IconDownload size={16} />
        </button>
        {art.caption && (
          <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>{art.caption}</div>
        )}
      </div>
    );
  }

  if (art.artifact_type === "voice") {
    return <VoiceArtifact displayUrl={displayUrl} fallbackUrl={resolveAttachmentUrl(art, apiBaseUrl).fallbackUrl} caption={art.caption} />;
  }

  const FileIcon = getFileTypeIcon(art.name || "");
  const sizeStr = art.size != null
    ? art.size > 1048576 ? `${(art.size / 1048576).toFixed(1)} MB` : `${(art.size / 1024).toFixed(1)} KB`
    : "";
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 10,
      padding: "10px 14px", borderRadius: 10, border: "1px solid var(--line)",
      fontSize: 13, marginBottom: 4, cursor: "pointer",
      background: "var(--panel)",
      transition: "background 0.15s",
    }}
      onClick={() => {
        if (_artifactClickTimer) clearTimeout(_artifactClickTimer);
        _artifactClickTimer = setTimeout(async () => {
          try {
            const savedPath = await downloadFile(downloadUrl, art.name || "file");
            await showInFolder(savedPath);
          } catch (err) {
            logger.error("Chat", "文件下载失败", { error: String(err) });
          }
        }, 250);
      }}
      onDoubleClick={() => {
        if (_artifactClickTimer) { clearTimeout(_artifactClickTimer); _artifactClickTimer = null; }
        (async () => {
          try {
            const savedPath = await downloadFile(downloadUrl, art.name || "file");
            await openFileWithDefault(savedPath);
          } catch (err) {
            logger.error("Chat", "文件打开失败", { error: String(err) });
          }
        })();
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(37,99,235,0.08)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--panel)"; }}
    >
      <FileIcon size={28} />
      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{art.name}</span>
        <span style={{ fontSize: 11, opacity: 0.5 }}>
          {sizeStr}{sizeStr && art.caption ? " · " : ""}{art.caption || ""}
        </span>
      </div>
      <IconDownload size={14} style={{ opacity: 0.4, flexShrink: 0 }} />
    </div>
  );
}

