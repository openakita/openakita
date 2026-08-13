import { beforeEach, describe, expect, it, vi } from "vitest";

import { openExternalUrl } from "../../platform";
import { safeFetch } from "../../providers";
import {
  connectOpenAkitaAccount,
  disconnectOpenAkitaAccount,
  loadAccountCapability,
  refreshOpenAkitaAccountEntitlements,
} from "../accountLogin";
import { ACCOUNT_STATUS_CHANGED_EVENT } from "../accountStatusEvents";

vi.mock("../../platform", () => ({
  openExternalUrl: vi.fn(),
}));

vi.mock("../../providers", () => ({
  safeFetch: vi.fn(),
}));

const response = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { "Content-Type": "application/json" },
});

describe("account login flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("opens OAuth, waits for completion, and publishes the signed-in snapshot", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(response({
        attempt_id: "attempt/1",
        authorization_url: "https://account.example/authorize",
      }))
      .mockResolvedValueOnce(response({ status: "complete" }))
      .mockResolvedValueOnce(response({
        status: "active",
        profile: { email: "user@example.com" },
      }));

    const listener = vi.fn();
    window.addEventListener(ACCOUNT_STATUS_CHANGED_EVENT, listener);
    try {
      const snapshot = await connectOpenAkitaAccount("http://localhost:18900", {
        pollIntervalMs: 0,
      });

      expect(openExternalUrl).toHaveBeenCalledWith("https://account.example/authorize");
      expect(safeFetch).toHaveBeenNthCalledWith(
        2,
        "http://localhost:18900/api/account/login/status/attempt%2F1",
      );
      expect(snapshot).toEqual({
        status: "active",
        profile: { email: "user@example.com" },
      });
      expect(listener).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener(ACCOUNT_STATUS_CHANGED_EVENT, listener);
    }
  });

  it("loads the backend account capability before rendering provider UI", async () => {
    vi.mocked(safeFetch).mockResolvedValueOnce(response({
      enabled: false,
      mode: "disabled",
      provider: null,
      display_name: null,
      supports_entitlements: false,
    }));

    const capability = await loadAccountCapability("http://localhost:18900");

    expect(safeFetch).toHaveBeenCalledWith(
      "http://localhost:18900/api/account/capability",
    );
    expect(capability.enabled).toBe(false);
    expect(capability.mode).toBe("disabled");
  });

  it("surfaces a failed OAuth attempt", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(response({
        attempt_id: "attempt-2",
        authorization_url: "https://account.example/authorize",
      }))
      .mockResolvedValueOnce(response({ status: "failed", error: "Access denied" }));

    await expect(connectOpenAkitaAccount("http://localhost:18900", {
      pollIntervalMs: 0,
    })).rejects.toThrow("Access denied");
  });

  it("reuses an in-progress login instead of opening OAuth twice", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(response({
        attempt_id: "attempt-3",
        authorization_url: "https://account.example/authorize",
      }))
      .mockResolvedValueOnce(response({ status: "complete" }))
      .mockResolvedValueOnce(response({ status: "active" }));

    const first = connectOpenAkitaAccount("http://localhost:18900", { pollIntervalMs: 0 });
    const second = connectOpenAkitaAccount("http://localhost:18900", { pollIntervalMs: 0 });

    expect(second).toBe(first);
    await first;
    expect(openExternalUrl).toHaveBeenCalledOnce();
  });

  it("refreshes entitlements and publishes the latest status", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(response({ ok: true }))
      .mockResolvedValueOnce(response({ status: "active", fetched_at: "2026-08-10" }));

    const listener = vi.fn();
    window.addEventListener(ACCOUNT_STATUS_CHANGED_EVENT, listener);
    try {
      const snapshot = await refreshOpenAkitaAccountEntitlements("http://localhost:18900");

      expect(safeFetch).toHaveBeenNthCalledWith(
        1,
        "http://localhost:18900/api/account/entitlements/refresh",
        { method: "POST" },
      );
      expect(snapshot.fetched_at).toBe("2026-08-10");
      expect(listener).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener(ACCOUNT_STATUS_CHANGED_EVENT, listener);
    }
  });

  it("signs out locally without opening a browser", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(response({ end_session_url: "https://account.example/" }))
      .mockResolvedValueOnce(response({ status: "signed_out" }));

    const snapshot = await disconnectOpenAkitaAccount("http://localhost:18900");

    expect(snapshot.status).toBe("signed_out");
    expect(openExternalUrl).not.toHaveBeenCalled();
  });
});
