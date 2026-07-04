import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

import { StaleBundleBanner } from "../StaleBundleBanner";

function makeFetchReturning(buildId: string): typeof fetch {
  return vi.fn(async () =>
    ({
      ok: true,
      status: 200,
      json: async () => ({ build_id: buildId }),
    }) as unknown as Response,
  ) as unknown as typeof fetch;
}

describe("StaleBundleBanner", () => {
  it("shows the banner when the backend build_id drifts away from the bundle", async () => {
    vi.useFakeTimers();
    const fetchImpl = makeFetchReturning("server-NEW");
    render(
      <StaleBundleBanner
        bundleId="bundle-OLD"
        apiBase="http://test"
        pollMs={1000}
        initialDelayMs={10}
        fetchImpl={fetchImpl}
      />,
    );
    expect(screen.queryByTestId("stale-bundle-banner")).toBeNull();

    // Drive the initial-delay timer + flush the awaited fetch.
    await act(async () => {
      vi.advanceTimersByTime(15);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://test/api/build-info",
      expect.objectContaining({ method: "GET" }),
    );
    expect(screen.getByTestId("stale-bundle-banner")).toBeInTheDocument();
    expect(screen.getByText("新版本可用，请刷新页面")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("stays hidden in dev mode when bundle id is the dev-<timestamp> sentinel", async () => {
    // smoke-banner regression guard: vite.config.ts falls back to
    // ``dev-<Date.now().toString(36)>`` when VITE_BUILD_ID is
    // absent (local ``npm run dev``). The backend returns its
    // package version, so the comparison would permanently
    // mismatch and lock the banner on. The component must
    // short-circuit before issuing any fetch.
    vi.useFakeTimers();
    const fetchImpl = makeFetchReturning("1.27.9");
    render(
      <StaleBundleBanner
        bundleId="dev-mfh3xyz"
        apiBase="http://test"
        pollMs={1000}
        initialDelayMs={10}
        fetchImpl={fetchImpl}
      />,
    );

    await act(async () => {
      vi.advanceTimersByTime(120_000);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    // No fetch must have been issued; no banner must be in the DOM.
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(screen.queryByTestId("stale-bundle-banner")).toBeNull();
    vi.useRealTimers();
  });

  it("stays hidden when the backend build_id matches the bundle", async () => {
    vi.useFakeTimers();
    const fetchImpl = makeFetchReturning("bundle-SAME");
    render(
      <StaleBundleBanner
        bundleId="bundle-SAME"
        apiBase=""
        pollMs={1000}
        initialDelayMs={10}
        fetchImpl={fetchImpl}
      />,
    );
    await act(async () => {
      vi.advanceTimersByTime(15);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("stale-bundle-banner")).toBeNull();
    vi.useRealTimers();
  });

  it("pushes app content down by setting body paddingTop when banner becomes stale", async () => {
    vi.useFakeTimers();
    const fetchImpl = makeFetchReturning("server-NEW");
    document.body.style.paddingTop = "";
    document.documentElement.style.removeProperty("--app-banner-height");
    const { unmount } = render(
      <StaleBundleBanner
        bundleId="bundle-OLD"
        apiBase="http://test"
        pollMs={1000}
        initialDelayMs={10}
        fetchImpl={fetchImpl}
      />,
    );
    await act(async () => {
      vi.advanceTimersByTime(15);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    // Body padding-top + CSS variable both populated to reserve banner space.
    expect(document.body.style.paddingTop).toBe("44px");
    expect(document.documentElement.style.getPropertyValue("--app-banner-height")).toBe("44px");
    unmount();
    expect(document.body.style.paddingTop).toBe("");
    vi.useRealTimers();
  });
});
