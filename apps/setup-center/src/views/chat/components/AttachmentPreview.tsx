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
  const { displayUrl: previewUrl, downloadUrl } = att.type === "image"
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
  const sizeStr = att.size ? `${(att.size / 1024).toFixed(1)} KB` : "";
  const statusText = att.uploadStatus === "uploading" ? "上传中" : att.uploadStatus === "failed" ? "上传失败" : "";
  const statusColor = att.uploadStatus === "failed" ? "var(--danger)" : "var(--muted)";
  return (
    <div style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 28px 6px 10px", borderRadius: 10, border: "1px solid var(--line)", fontSize: 12 }}>
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
      <span style={{ display: "inline-flex", alignItems: "center" }}>{icon}</span>
      <span style={{ fontWeight: 600 }}>{att.name}</span>
      {sizeStr && <span style={{ opacity: 0.5 }}>{sizeStr}</span>}
      {statusText && <span style={{ color: statusColor, fontSize: 11 }}>{statusText}</span>}
    </div>
  );
}
