import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { SlashCommand } from "../utils/chatTypes";
import {
  IconRefresh, IconClipboard, IconTrash, IconZap,
  IconMask, IconBot, IconUsers, IconHelp,
} from "../../../icons";

export function SlashCommandPanel({
  commands,
  filter,
  onSelect,
  selectedIdx,
}: {
  commands: SlashCommand[];
  filter: string;
  onSelect: (cmd: SlashCommand) => void;
  selectedIdx: number;
}) {
  const { t } = useTranslation();
  const filtered = useMemo(() => {
    const q = filter.toLowerCase();
    return commands.filter((c) => c.id.includes(q) || c.label.includes(q) || c.description.includes(q));
  }, [commands, filter]);

  return (
    <div
      style={{
        position: "absolute",
        bottom: "100%",
        left: 0,
        right: 0,
        marginBottom: 6,
        maxHeight: 260,
        overflow: "auto",
        border: "1px solid var(--line)",
        borderRadius: 14,
        background: "var(--panel2)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
        boxShadow: "0 -12px 48px rgba(17,24,39,0.18)",
        zIndex: 100,
      }}
    >
      {filtered.length === 0 ? (
        <div style={{ padding: "10px 16px", fontSize: 13, opacity: 0.5, textAlign: "center" }}>
          {t("chat.noMatchingCommand", "无匹配命令")}
        </div>
      ) : filtered.map((cmd, idx) => (
        <div
          key={cmd.id}
          onClick={() => onSelect(cmd)}
          style={{
            padding: "10px 14px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 10,
            background: idx === selectedIdx ? "rgba(37,99,235,0.14)" : "transparent",
            borderTop: idx === 0 ? "none" : "1px solid rgba(17,24,39,0.1)",
          }}
        >
          <span style={{ fontSize: 16, opacity: 0.7, display: "inline-flex", alignItems: "center", justifyContent: "center", width: 20, minWidth: 20 }}>
            {cmd.id === "model" ? <IconRefresh size={16} /> :
             cmd.id === "plan" ? <IconClipboard size={16} /> :
             cmd.id === "clear" ? <IconTrash size={16} /> :
             cmd.id === "skill" ? <IconZap size={16} /> :
             cmd.id === "persona" ? <IconMask size={16} /> :
             cmd.id === "agent" ? <IconBot size={16} /> :
             cmd.id === "agents" ? <IconUsers size={16} /> :
             cmd.id === "org" ? <IconUsers size={16} /> :
             cmd.id === "help" ? <IconHelp size={16} /> :
             <span style={{ fontSize: 14 }}>/</span>}
          </span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 13 }}>/{cmd.id} <span style={{ fontWeight: 400, opacity: 0.6 }}>{cmd.label}</span></div>
            <div style={{ fontSize: 12, opacity: 0.5 }}>{cmd.description}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
