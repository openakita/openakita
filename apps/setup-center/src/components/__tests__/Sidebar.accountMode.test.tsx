import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../../i18n";
import { Sidebar } from "../Sidebar";
import { loadAccountCapability } from "../../utils/accountLogin";

vi.mock("../../utils/accountLogin", () => ({
  connectOpenAkitaAccount: vi.fn(),
  disconnectOpenAkitaAccount: vi.fn(),
  loadAccountCapability: vi.fn(),
  refreshOpenAkitaAccountEntitlements: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    loading: vi.fn(),
    success: vi.fn(),
  },
}));

function renderSidebar() {
  return render(
    <Sidebar
      collapsed={false}
      onToggleCollapsed={vi.fn()}
      view="chat"
      onViewChange={vi.fn()}
      configMode={false}
      onEnterConfig={vi.fn()}
      onExitConfig={vi.fn()}
      steps={[]}
      stepId="workspace"
      onStepChange={vi.fn()}
      disabledViews={[]}
      storeVisible={false}
      serviceRunning
      onRefreshStatus={vi.fn(async () => undefined)}
      httpApiBase="http://localhost:18900"
    />,
  );
}

describe("Sidebar account distribution mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
  });

  it("renders an account-free application menu when the capability is disabled", async () => {
    vi.mocked(loadAccountCapability).mockResolvedValue({
      enabled: false,
      mode: "disabled",
      provider: null,
      display_name: null,
      supports_entitlements: false,
    });

    renderSidebar();

    const menuButton = await screen.findByRole("button", { name: /应用菜单|App menu/i });
    expect(screen.queryByText(/未登录|Signed out/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/连接账户|Connect account/i)).not.toBeInTheDocument();

    fireEvent.click(menuButton);
    await waitFor(() => {
      expect(screen.getByRole("menu")).toHaveAccessibleName(/应用菜单|App menu/i);
    });
    expect(screen.getByText(/配置|Config/i)).toBeInTheDocument();
    expect(screen.queryByText(/退出登录|Sign out/i)).not.toBeInTheDocument();
  });

  it("uses neutral account branding for a custom provider", async () => {
    vi.mocked(loadAccountCapability).mockResolvedValue({
      enabled: true,
      mode: "custom",
      provider: "vendor-id",
      display_name: "Vendor Account",
      supports_entitlements: true,
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return url.endsWith("/api/account/status")
        ? new Response(JSON.stringify({ status: "active", profile: {} }), { status: 200 })
        : new Response("[]", { status: 200 });
    }));

    const { container } = renderSidebar();

    expect((await screen.findAllByText("Vendor Account")).length).toBeGreaterThan(0);
    expect(container.querySelector("img.sidebarAccountAvatar")).toBeNull();
  });
});
