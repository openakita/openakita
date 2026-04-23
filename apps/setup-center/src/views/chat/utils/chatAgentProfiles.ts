export const DEFAULT_CHAT_AGENT_PROFILE_ID = "default";

export type ChatAgentProfile = {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  supports_primary_chat: boolean;
  name_i18n?: Record<string, string>;
  description_i18n?: Record<string, string>;
  preferred_endpoint?: string | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeI18n(value: unknown): Record<string, string> | undefined {
  if (!isRecord(value)) return undefined;
  if (!Object.values(value).every((entry) => typeof entry === "string")) return undefined;
  return value as Record<string, string>;
}

export function normalizeChatAgentProfiles(raw: unknown[]): ChatAgentProfile[] {
  const records = Array.isArray(raw) ? raw : [];
  return records
    .filter(isRecord)
    .map((item) => {
      const id = typeof item.id === "string" ? item.id.trim() : "";
      const name = typeof item.name === "string" && item.name.trim() ? item.name.trim() : id;
      const description = typeof item.description === "string" ? item.description : "";
      const icon = typeof item.icon === "string" && item.icon.trim() ? item.icon : "🤖";
      const color = typeof item.color === "string" && item.color.trim() ? item.color : "#6b7280";
      const preferredEndpoint =
        typeof item.preferred_endpoint === "string" || item.preferred_endpoint === null
          ? item.preferred_endpoint
          : null;
      const profile: ChatAgentProfile = {
        id,
        name,
        description,
        icon,
        color,
        supports_primary_chat: item.supports_primary_chat === true,
        preferred_endpoint: preferredEndpoint,
      };
      const nameI18n = normalizeI18n(item.name_i18n);
      if (nameI18n) profile.name_i18n = nameI18n;
      const descriptionI18n = normalizeI18n(item.description_i18n);
      if (descriptionI18n) profile.description_i18n = descriptionI18n;
      return profile;
    })
    .filter((profile) => profile.id !== "");
}

export function getChatSelectableProfiles(profiles: ChatAgentProfile[]): ChatAgentProfile[] {
  return profiles.filter((profile) => profile.supports_primary_chat);
}

export function normalizePrimaryChatProfileId(
  selectedId: string,
  profiles: ChatAgentProfile[],
): string {
  if (selectedId === DEFAULT_CHAT_AGENT_PROFILE_ID) return DEFAULT_CHAT_AGENT_PROFILE_ID;
  return getChatSelectableProfiles(profiles).some((profile) => profile.id === selectedId)
    ? selectedId
    : DEFAULT_CHAT_AGENT_PROFILE_ID;
}
