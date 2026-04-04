import type { ChatAttachment } from "../utils/chatTypes";
import {
  IconX, IconMic, IconPlay, IconImage, IconPaperclip,
} from "../../../icons";

export function AttachmentPreview({ att, onRemove }: { att: ChatAttachment; onRemove?: () => void }) {
  if (att.type === "image" && att.previewUrl) {
    return (
      <div style={{ position: "relative", display: "inline-block" }}>
        <img src={att.previewUrl} alt={att.name} style={{ width: 80, height: 80, objectFit: "cover", display: "block", borderRadius: 10, border: "1px solid var(--line)" }} />
        {onRemove && (
          <button
            onClick={onRemove}
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
  return (
    <div style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 28px 6px 10px", borderRadius: 10, border: "1px solid var(--line)", fontSize: 12 }}>
      {onRemove && (
        <button
          onClick={onRemove}
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
    </div>
  );
}
