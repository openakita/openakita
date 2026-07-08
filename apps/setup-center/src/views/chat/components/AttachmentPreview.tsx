import type { ChatAttachment } from "../utils/chatTypes";
import { appendAuthToken } from "../utils/chatHelpers";
import {
  IconX, IconMic, IconPlay, IconImage, IconPaperclip,
} from "../../../icons";

function normalizeAttachmentUrl(raw: string, apiBaseUrl?: string): string {
  if (raw.startsWith("data:") || raw.startsWith("blob:")) return raw;
  if (raw.startsWith("http")) return appendAuthToken(raw);
  if (raw.startsWith("/")) return appendAuthToken(`${apiBaseUrl || ""}${raw}`);
  return raw;
}

function resolvePreviewUrls(att: ChatAttachment, apiBaseUrl?: string): { displayUrl: string; downloadUrl: string } {
  const displayRaw = att.previewUrl || att.url || "";
  const downloadRaw = att.url || att.previewUrl || "";
  const displayUrl = normalizeAttachmentUrl(displayRaw, apiBaseUrl);
  const downloadUrl = normalizeAttachmentUrl(downloadRaw, apiBaseUrl);
  if (displayUrl || downloadUrl) {
    return { displayUrl: displayUrl || downloadUrl, downloadUrl: downloadUrl || displayUrl };
  }
  if (att.localPath) {
    const fileUrl = appendAuthToken(`${apiBaseUrl || ""}/api/files?path=${encodeURIComponent(att.localPath)}`);
    return { displayUrl: fileUrl, downloadUrl: fileUrl };
  }
  return { displayUrl: "", downloadUrl: "" };
}

function formatAttachmentSize(bytes: number | null | undefined): string {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return "";
  if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${Math.round(n)} B`;
}

export function AttachmentPreview({
  att,
  onRemove,
  apiBaseUrl,
  onImagePreview,
}: {
  att: ChatAttachment;
  onRemove?: () => void;
  apiBaseUrl?: string;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
}) {
  const isUploading = att.uploadStatus === "uploading";
  const rawProgress = Number(att.uploadProgress);
  const hasProgress = Number.isFinite(rawProgress);
  const progressValue = hasProgress ? Math.max(0, Math.min(100, Math.round(rawProgress * 100))) : undefined;
  const { displayUrl: previewUrl, downloadUrl } = att.type === "image" && !isUploading
    ? resolvePreviewUrls(att, apiBaseUrl)
    : { displayUrl: "", downloadUrl: "" };
  if (att.type === "image" && previewUrl) {
    return (
      <div style={{ position: "relative", display: "inline-block" }}>
        <img
          src={previewUrl}
          alt={att.name}
          role={onImagePreview ? "button" : undefined}
          tabIndex={onImagePreview ? 0 : undefined}
          style={{ width: 80, height: 80, objectFit: "cover", display: "block", borderRadius: 10, border: "1px solid var(--line)", cursor: onImagePreview ? "pointer" : "default" }}
          onClick={() => onImagePreview?.(previewUrl, downloadUrl, att.name || "image")}
          onKeyDown={(e) => {
            if (!onImagePreview || (e.key !== "Enter" && e.key !== " ")) return;
            e.preventDefault();
            onImagePreview(previewUrl, downloadUrl, att.name || "image");
          }}
        />
        {onRemove && (
          <button
            onClick={(e) => { e.stopPropagation(); onRemove(); }}
            style={{
              position: "absolute", top: -6, right: -6,
              width: 22, height: 22, borderRadius: 11,
              border: "2px solid #fff", background: "var(--danger)", color: "#fff",
              fontSize: 11, cursor: "pointer", display: "grid", placeItems: "center",
              boxShadow: "0 1px 4px rgba(0,0,0,0.18)", zIndex: 2, padding: 0, lineHeight: 1,
            }}
          >
            <IconX size={11} />
          </button>
        )}
      </div>
    );
  }
  const icon = att.type === "voice" ? <IconMic size={14} /> : att.type === "video" ? <IconPlay size={14} /> : att.type === "image" ? <IconImage size={14} /> : <IconPaperclip size={14} />;
  const sizeStr = formatAttachmentSize(att.size);
  const statusText = isUploading ? "处理中" : att.uploadStatus === "failed" ? "处理失败" : "";
  const statusColor = att.uploadStatus === "failed" ? "var(--danger)" : "var(--muted)";
  return (
    <div style={{ position: "relative", display: "inline-flex", flexDirection: "column", gap: 4, padding: "6px 28px 6px 10px", borderRadius: 10, border: "1px solid var(--line)", fontSize: 12, minWidth: isUploading ? 180 : undefined }}>
      {onRemove && (
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          style={{
            position: "absolute", top: -6, right: -6,
            width: 22, height: 22, borderRadius: 11,
            border: "2px solid #fff", background: "var(--danger)", color: "#fff",
            fontSize: 11, cursor: "pointer", display: "grid", placeItems: "center",
            boxShadow: "0 1px 4px rgba(0,0,0,0.18)", zIndex: 2, padding: 0, lineHeight: 1,
          }}
        >
          <IconX size={11} />
        </button>
      )}
      <div style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <span style={{ display: "inline-flex", alignItems: "center", flexShrink: 0 }}>{icon}</span>
        <span style={{ fontWeight: 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{att.name}</span>
        {sizeStr && <span style={{ opacity: 0.5, flexShrink: 0 }}>{sizeStr}</span>}
        {statusText && <span style={{ color: statusColor, fontSize: 11, flexShrink: 0 }}>{statusText}</span>}
      </div>
      {isUploading && (
        <progress
          aria-label="附件处理进度"
          max={100}
          value={progressValue}
          style={{ width: "100%", height: 4, display: "block", accentColor: "var(--brand)" }}
        />
      )}
    </div>
  );
}
