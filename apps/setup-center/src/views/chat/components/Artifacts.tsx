import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ChatArtifact } from "../utils/chatTypes";
import { appendAuthToken } from "../utils/chatHelpers";
import { downloadFile, openFileWithDefault, showInFolder, logger, getAssetUrl } from "../../../platform";
import { IconDownload, getFileTypeIcon, IconMic } from "../../../icons";

let _artifactClickTimer: ReturnType<typeof setTimeout> | null = null;

function VoiceArtifact({ src, caption }: { src: string; caption?: string }) {
  const [error, setError] = useState(false);
  return (
    <div style={{ marginBottom: 8 }}>
      <audio
        controls
        preload="metadata"
        src={src}
        style={{ maxWidth: "100%" }}
        onError={() => setError(true)}
      />
      {error && (
        <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 4 }}>
          音频加载失败，请检查文件是否存在或格式是否支持
        </div>
      )}
      {caption && (
        <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>{caption}</div>
      )}
    </div>
  );
}

function ArtifactItem({ art, displayUrl, downloadUrl, onImagePreview }: {
  art: ChatArtifact;
  displayUrl: string;
  downloadUrl: string;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
}) {
  const { t } = useTranslation();

  if (art.artifact_type === "image") {
    return (
      <div style={{ marginBottom: 8, position: "relative", display: "inline-block" }}>
        <img
          src={displayUrl}
          alt={art.caption || art.name}
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
              onImagePreview?.(displayUrl, downloadUrl, art.name || "image");
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
    return <VoiceArtifact src={displayUrl} caption={art.caption} />;
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

export function ArtifactList({ artifacts, apiBaseUrl, onImagePreview }: {
  artifacts: ChatArtifact[];
  apiBaseUrl?: string;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
}) {
  return (
    <div style={{ marginTop: 8 }}>
      {artifacts.map((art, i) => {
        const httpUrl = (() => {
          const rawUrl = art.file_url.startsWith("http")
            ? art.file_url
            : `${apiBaseUrl || ""}${art.file_url}`;
          return appendAuthToken(rawUrl);
        })();
        const displayUrl = getAssetUrl(art.path) || httpUrl;
        return <ArtifactItem key={i} art={art} displayUrl={displayUrl} downloadUrl={httpUrl} onImagePreview={onImagePreview} />;
      })}
    </div>
  );
}
