import { openExternalUrl } from "../platform";
import { safeFetch } from "../providers";
import {
  dispatchAccountStatusChanged,
  type AccountStatusSummary,
} from "./accountStatusEvents";

type LoginStart = {
  attempt_id: string;
  authorization_url: string;
};

type LoginProgress = {
  status: string;
  error?: string;
};

type AccountLoginOptions = {
  pollIntervalMs?: number;
};

export type AccountCapability = {
  enabled: boolean;
  mode: "openakita" | "custom" | "disabled";
  provider: string | null;
  display_name: string | null;
  supports_entitlements: boolean;
};

let activeLogin: Promise<AccountStatusSummary> | null = null;

export async function loadAccountCapability(apiBaseUrl: string): Promise<AccountCapability> {
  const response = await safeFetch(`${apiBaseUrl}/api/account/capability`);
  return await response.json() as AccountCapability;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function runAccountLogin(
  apiBaseUrl: string,
  pollIntervalMs: number,
): Promise<AccountStatusSummary> {
  const response = await safeFetch(`${apiBaseUrl}/api/account/login/start`, { method: "POST" });
  const attempt = await response.json() as LoginStart;
  if (!attempt.attempt_id || !attempt.authorization_url) {
    throw new Error("The account service returned an invalid login attempt.");
  }

  await openExternalUrl(attempt.authorization_url);

  while (true) {
    await delay(pollIntervalMs);
    const poll = await safeFetch(
      `${apiBaseUrl}/api/account/login/status/${encodeURIComponent(attempt.attempt_id)}`,
    );
    const result = await poll.json() as LoginProgress;
    if (result.status === "complete") {
      const statusResponse = await safeFetch(`${apiBaseUrl}/api/account/status`);
      const snapshot = await statusResponse.json() as AccountStatusSummary;
      dispatchAccountStatusChanged(snapshot);
      return snapshot;
    }
    if (result.status === "failed" || result.status === "expired") {
      throw new Error(result.error || "account_login_expired");
    }
  }
}

async function loadAndPublishAccountStatus(apiBaseUrl: string): Promise<AccountStatusSummary> {
  const statusResponse = await safeFetch(`${apiBaseUrl}/api/account/status`);
  const snapshot = await statusResponse.json() as AccountStatusSummary;
  dispatchAccountStatusChanged(snapshot);
  return snapshot;
}

export function connectOpenAkitaAccount(
  apiBaseUrl: string,
  options: AccountLoginOptions = {},
): Promise<AccountStatusSummary> {
  if (activeLogin) return activeLogin;

  const operation = runAccountLogin(apiBaseUrl, options.pollIntervalMs ?? 1_000);
  activeLogin = operation;
  const clearOperation = () => {
    if (activeLogin === operation) activeLogin = null;
  };
  void operation.then(clearOperation, clearOperation);
  return operation;
}

export async function refreshOpenAkitaAccountEntitlements(
  apiBaseUrl: string,
): Promise<AccountStatusSummary> {
  await safeFetch(`${apiBaseUrl}/api/account/entitlements/refresh`, { method: "POST" });
  return loadAndPublishAccountStatus(apiBaseUrl);
}

export async function disconnectOpenAkitaAccount(
  apiBaseUrl: string,
): Promise<AccountStatusSummary> {
  await safeFetch(`${apiBaseUrl}/api/account/logout`, { method: "POST" });
  return loadAndPublishAccountStatus(apiBaseUrl);
}
