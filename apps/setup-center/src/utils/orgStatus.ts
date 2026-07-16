import type { TFunction } from "i18next";

export const ORG_STATUS_I18N_KEYS: Record<string, string> = {
  dormant: "org.orgStatus.dormant",
  created: "org.orgStatus.created",
  active: "org.orgStatus.active",
  running: "org.orgStatus.running",
  paused: "org.orgStatus.paused",
  stopped: "org.orgStatus.stopped",
  archived: "org.orgStatus.archived",
  deleted: "org.orgStatus.deleted",
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function localizeOrgStatus(t: TFunction, value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  const normalized = raw.toLowerCase();
  const key = ORG_STATUS_I18N_KEYS[normalized];
  return key ? t(key) : raw || t("org.orgStatus.unknown");
}

export function localizeOrgCommandStateError(
  t: TFunction,
  payload: unknown,
): string | null {
  const outer = asRecord(payload);
  const detail = asRecord(outer.detail);
  const errorCode = String(
    outer.error_code || outer.code || detail.error_code || detail.code || "",
  );
  if (errorCode !== "org_not_runnable") return null;

  const status = outer.org_status || detail.org_status;
  return t("org.chat.commandUnavailableStatus", {
    status: localizeOrgStatus(t, status),
  });
}
