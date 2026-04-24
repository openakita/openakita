import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toPng } from "html-to-image";
import { IS_TAURI, saveFileDialog, writeFile } from "../platform";
import logoUrl from "../assets/logo.png";
import { ModalOverlay } from "./ModalOverlay";

interface PosterProps {
  type: "invite" | "achievement";
  userName: string;
  userAvatar?: string;
  tierBadge: string;
  tier: string;
  inviteCode?: string;
  inviteUrl?: string;
  pioneerNumber?: number;
  publishCount?: number;
  downloadCount?: number;
  apBalance?: number;
  onClose: () => void;
}

const TIER_COLORS: Record<string, { bg: string; text: string; accent: string }> = {
  explorer:    { bg: "#e8f5e9", text: "#2e7d32", accent: "#66bb6a" },
  contributor: { bg: "#e3f2fd", text: "#1565c0", accent: "#42a5f5" },
  builder:     { bg: "#fff3e0", text: "#e65100", accent: "#ffa726" },
  champion:    { bg: "#fce4ec", text: "#b71c1c", accent: "#ef5350" },
};

async function savePngTauri(dataUrl: string, defaultName: string): Promise<boolean> {
  try {
    const path = await saveFileDialog({
      defaultPath: defaultName,
      filters: [{ name: "PNG Image", extensions: ["png"] }],
    });
    if (!path) return false;
    const base64 = dataUrl.split(",")[1];
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    await writeFile(path, bytes);
    return true;
  } catch {
    return false;
  }
}

function savePngWeb(dataUrl: string, filename: string) {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

export function PosterGenerator(props: PosterProps) {
  const {
    type, userName, userAvatar, tierBadge, tier,
    inviteCode, inviteUrl, pioneerNumber,
    publishCount, downloadCount, apBalance,
    onClose,
  } = props;
  const { t } = useTranslation();
  const posterRef = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const palette = TIER_COLORS[tier] || TIER_COLORS.explorer;
  const isInvite = type === "invite";
  const posterW = 600;
  const posterH = isInvite ? 900 : 400;

  const handleExport = async () => {
    if (!posterRef.current) return;
    setExporting(true);
    setMessage(null);
    try {
      const dataUrl = await toPng(posterRef.current, {
        cacheBust: true,
        pixelRatio: 2,
        width: posterW,
        height: posterH,
      });
      const filename = `openakita-${type}-${Date.now()}.png`;
      if (IS_TAURI) {
        const saved = await savePngTauri(dataUrl, filename);
        if (!saved) {
          savePngWeb(dataUrl, filename);
        }
      } else {
        savePngWeb(dataUrl, filename);
      }
      setMessage(t("poster.exported"));
    } catch {
      setMessage(t("poster.exportFailed"));
    } finally {
      setExporting(false);
    }
  };

  return (
    <ModalOverlay onClose={onClose} className="" style={overlayStyle}>
      <div style={modalStyle}>
        {/* Poster render target */}
        <div ref={posterRef} style={{ ...posterBaseStyle, width: posterW, height: posterH }}>
          {isInvite ? (
            <InvitePoster
              userName={userName}
              userAvatar={userAvatar}
              tierBadge={tierBadge}
              tier={tier}
              inviteCode={inviteCode}
              inviteUrl={inviteUrl}
              palette={palette}
              t={t}
            />
          ) : (
            <AchievementPoster
              userName={userName}
              userAvatar={userAvatar}
              tierBadge={tierBadge}
              tier={tier}
              pioneerNumber={pioneerNumber}
              publishCount={publishCount}
              downloadCount={downloadCount}
              apBalance={apBalance}
              palette={palette}
              t={t}
            />
          )}
        </div>

        {/* Action bar */}
        <div style={actionBarStyle}>
          {message && (
            <span style={{ fontSize: 13, color: message === t("poster.exported") ? "#4caf50" : "#f44336" }}>
              {message}
            </span>
          )}
          <div style={{ flex: 1 }} />
          <button
            style={{ ...btnStyle, background: "#1976d2", color: "#fff", opacity: exporting ? 0.6 : 1 }}
            disabled={exporting}
            onClick={handleExport}
          >
            {exporting ? "..." : t("poster.exportPng")}
          </button>
          <button style={{ ...btnStyle, background: "#e0e0e0", color: "#333" }} onClick={onClose}>
            {t("poster.close")}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}

/* ─── Sub-components ──────────────────────────────────────────── */

interface InviteSubProps {
  userName: string;
  userAvatar?: string;
  tierBadge: string;
  tier: string;
  inviteCode?: string;
  inviteUrl?: string;
  palette: { bg: string; text: string; accent: string };
  t: (key: string) => string;
}

function InvitePoster({ userName, userAvatar, tierBadge, inviteCode, inviteUrl, palette, t }: InviteSubProps) {
  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", alignItems: "center", padding: 40 }}>
      {/* Header */}
      <img src={logoUrl} alt="logo" style={{ width: 64, height: 64, borderRadius: 12, marginBottom: 12 }} />
      <div style={{ fontSize: 28, fontWeight: 700, color: "#1a1a2e", letterSpacing: 1 }}>
        OpenAkita
      </div>
      <div style={{ fontSize: 15, color: "#666", marginTop: 4, marginBottom: 32 }}>
        {t("poster.inviteSlogan")}
      </div>

      {/* Divider */}
      <div style={{ width: "80%", height: 1, background: "linear-gradient(90deg, transparent, #ccc, transparent)", marginBottom: 32 }} />

      {/* User card */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 24 }}>
        <div style={avatarStyle(palette.accent)}>
          {userAvatar ? (
            <img src={userAvatar} alt="" style={{ width: "100%", height: "100%", borderRadius: "50%", objectFit: "cover" }} />
          ) : (
            <span style={{ fontSize: 28, fontWeight: 700, color: "#fff" }}>{userName.charAt(0).toUpperCase()}</span>
          )}
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, color: "#1a1a2e" }}>{userName}</div>
          <div style={{ ...tierBadgeStyle, background: palette.bg, color: palette.text, borderColor: palette.accent }}>
            {tierBadge}
          </div>
        </div>
      </div>

      <div style={{ flex: 1 }} />

      {/* QR/Invite area */}
      <div style={{
        width: 200, height: 200, borderRadius: 16, border: "2px dashed #bbb",
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        background: "#f9f9f9", marginBottom: 16,
      }}>
        <div style={{ fontSize: 13, color: "#999", textAlign: "center", padding: "0 16px" }}>
          {inviteUrl || inviteCode || "QR"}
        </div>
      </div>
      <div style={{ fontSize: 14, color: "#888" }}>
        {t("poster.scanToJoin")}
      </div>

      {inviteCode && (
        <div style={{ marginTop: 12, fontSize: 13, color: palette.text, fontFamily: "monospace", letterSpacing: 2 }}>
          {inviteCode}
        </div>
      )}
    </div>
  );
}

interface AchievementSubProps {
  userName: string;
  userAvatar?: string;
  tierBadge: string;
  tier: string;
  pioneerNumber?: number;
  publishCount?: number;
  downloadCount?: number;
  apBalance?: number;
  palette: { bg: string; text: string; accent: string };
  t: (key: string) => string;
}

function AchievementPoster({
  userName, userAvatar, tierBadge, pioneerNumber,
  publishCount, downloadCount, apBalance, palette, t,
}: AchievementSubProps) {
  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", padding: 32 }}>
      {/* Top row: logo + title */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
        <img src={logoUrl} alt="logo" style={{ width: 40, height: 40, borderRadius: 8 }} />
        <div style={{ fontSize: 20, fontWeight: 700, color: "#1a1a2e" }}>
          {t("poster.achievementTitle")}
        </div>
      </div>

      {/* User row */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 20 }}>
        <div style={avatarStyle(palette.accent)}>
          {userAvatar ? (
            <img src={userAvatar} alt="" style={{ width: "100%", height: "100%", borderRadius: "50%", objectFit: "cover" }} />
          ) : (
            <span style={{ fontSize: 24, fontWeight: 700, color: "#fff" }}>{userName.charAt(0).toUpperCase()}</span>
          )}
        </div>
        <div>
          <div style={{ fontSize: 18, fontWeight: 600, color: "#1a1a2e" }}>{userName}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
            <div style={{ ...tierBadgeStyle, background: palette.bg, color: palette.text, borderColor: palette.accent }}>
              {tierBadge}
            </div>
            {pioneerNumber != null && (
              <span style={{ fontSize: 13, color: "#888" }}>
                {t("poster.pioneer")} #{pioneerNumber}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Stats */}
      <div style={{
        display: "flex", gap: 16, background: "#f5f5f7", borderRadius: 12, padding: "16px 20px",
        justifyContent: "space-around",
      }}>
        <StatItem label={t("poster.publishCount")} value={publishCount ?? 0} color={palette.text} />
        <StatItem label={t("poster.downloadCount")} value={downloadCount ?? 0} color={palette.text} />
        <StatItem label={t("poster.apBalance")} value={apBalance ?? 0} color={palette.text} />
      </div>

      <div style={{ flex: 1 }} />

      {/* Footer */}
      <div style={{ textAlign: "center", fontSize: 12, color: "#aaa" }}>
        OpenAkita · Share Agents, Not Just Skills
      </div>
    </div>
  );
}

function StatItem({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ textAlign: "center", minWidth: 80 }}>
      <div style={{ fontSize: 24, fontWeight: 700, color }}>{value.toLocaleString()}</div>
      <div style={{ fontSize: 12, color: "#888", marginTop: 2 }}>{label}</div>
    </div>
  );
}

/* ─── Styles ──────────────────────────────────────────────────── */

const overlayStyle: React.CSSProperties = {
  position: "fixed", inset: 0, zIndex: 9999,
  background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)",
  display: "flex", alignItems: "center", justifyContent: "center",
};

const modalStyle: React.CSSProperties = {
  background: "#fff", borderRadius: 16, padding: 24,
  boxShadow: "0 12px 40px rgba(0,0,0,0.25)",
  display: "flex", flexDirection: "column", alignItems: "center",
  maxHeight: "90vh", overflow: "auto",
};

const posterBaseStyle: React.CSSProperties = {
  background: "linear-gradient(135deg, #ffffff 0%, #f0f4f8 100%)",
  borderRadius: 16, overflow: "hidden",
  fontFamily: "'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  color: "#1a1a2e",
};

const actionBarStyle: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 12,
  marginTop: 16, width: "100%",
};

const btnStyle: React.CSSProperties = {
  border: "none", borderRadius: 8, padding: "8px 20px",
  fontSize: 14, fontWeight: 600, cursor: "pointer",
  transition: "opacity 0.15s",
};

function avatarStyle(accent: string): React.CSSProperties {
  return {
    width: 56, height: 56, borderRadius: "50%",
    background: accent, display: "flex",
    alignItems: "center", justifyContent: "center",
    overflow: "hidden", flexShrink: 0,
  };
}

const tierBadgeStyle: React.CSSProperties = {
  display: "inline-block", fontSize: 12, fontWeight: 600,
  padding: "2px 10px", borderRadius: 999,
  border: "1px solid", marginTop: 4,
};

