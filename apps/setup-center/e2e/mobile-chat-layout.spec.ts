import { expect, test } from "@playwright/test";

for (const width of [320, 393, 430]) {
  test(`mobile chat controls stay inside a ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 320 ? 720 : 852 });
    await page.goto("/#/chat", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);

    const textarea = page.getByTestId("chat-input-textarea");
    const toolbar = page.getByTestId("chat-input-toolbar");

    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await expect(toolbar).toBeVisible();
    await expect(textarea).toHaveAttribute("placeholder", /^(输入消息\.\.\.|Type a message\.\.\.)$/);

    const layout = await page.evaluate(() => {
      const requiredSelectors = [
        ".topbar",
        '[data-testid="topbar-actions"]',
        '[data-testid="topbar-remote-access"]',
        '[data-testid="chat-input-pickers"]',
        '[data-testid="chat-input-toolbar"]',
        '[data-testid="chat-input-textarea"]',
      ];
      const optionalSelectors = ['[data-testid="chat-context-usage"]'].filter((selector) =>
        document.querySelector(selector),
      );
      const selectors = [...requiredSelectors, ...optionalSelectors];
      const boxes = selectors.map((selector) => {
        const element = document.querySelector<HTMLElement>(selector);
        if (!element) return { selector, missing: true };
        const rect = element.getBoundingClientRect();
        return {
          selector,
          left: rect.left,
          right: rect.right,
          width: rect.width,
          scrollWidth: element.scrollWidth,
          clientWidth: element.clientWidth,
        };
      });
      const toolbarIcons = Array.from(
        document.querySelectorAll<SVGElement>('[data-testid="chat-input-toolbar"] button > svg'),
      ).map((icon) => {
        const rect = icon.getBoundingClientRect();
        return {
          width: rect.width,
          height: rect.height,
          declaredWidth: Number(icon.getAttribute("width")) || 0,
          declaredHeight: Number(icon.getAttribute("height")) || 0,
        };
      });
      const visibleLabels = Array.from(
        document.querySelectorAll<HTMLElement>(".chatInputIconLabel"),
      ).filter((label) => getComputedStyle(label).display !== "none").length;
      const remoteIcon = document.querySelector<SVGElement>(
        '[data-testid="topbar-remote-access"] > svg',
      )?.getBoundingClientRect();
      const remoteLabel = document.querySelector<HTMLElement>(".topbarRemoteLabel");
      return {
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        boxes,
        toolbarIcons,
        visibleLabels,
        remoteIcon: remoteIcon ? { width: remoteIcon.width, height: remoteIcon.height } : null,
        remoteLabelVisible: remoteLabel ? getComputedStyle(remoteLabel).display !== "none" : null,
      };
    });

    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
    for (const box of layout.boxes) {
      expect(box.missing, `${box.selector} should be rendered`).not.toBe(true);
      expect(box.left, `${box.selector} should not overflow left`).toBeGreaterThanOrEqual(0);
      expect(box.right, `${box.selector} should not overflow right`).toBeLessThanOrEqual(
        layout.viewportWidth + 0.5,
      );
    }
    expect(layout.toolbarIcons.length).toBeGreaterThan(0);
    for (const icon of layout.toolbarIcons) {
      expect(icon.width + 0.5).toBeGreaterThanOrEqual(icon.declaredWidth);
      expect(icon.height + 0.5).toBeGreaterThanOrEqual(icon.declaredHeight);
    }
    expect(layout.visibleLabels).toBe(0);
    expect(layout.remoteIcon).toEqual({ width: 16, height: 16 });
    expect(layout.remoteLabelVisible).toBe(false);

    await page.getByTestId("chat-mode-trigger").click();
    const modeMenu = page.getByTestId("chat-mode-menu");
    await expect(modeMenu).toBeVisible();
    const menuBox = await modeMenu.boundingBox();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.x).toBeGreaterThanOrEqual(0);
    expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(layout.viewportWidth + 0.5);
  });
}

test("mobile organization menu stays inside the viewport", async ({ page }) => {
  const apiBase = "http://127.0.0.1:18900";
  const templatesResponse = await page.request.get(`${apiBase}/api/v2/orgs/templates`);
  expect(templatesResponse.status()).toBe(200);
  const templates = (await templatesResponse.json()) as Array<{ id: string }>;
  expect(templates.length).toBeGreaterThan(0);

  const createResponse = await page.request.post(`${apiBase}/api/v2/orgs/from-template`, {
    data: { template_id: templates[0].id, name: `mobile-menu-${Date.now()}` },
    headers: { "Content-Type": "application/json" },
  });
  expect(createResponse.status()).toBe(201);
  const orgId = ((await createResponse.json()) as { id: string }).id;

  try {
    await page.setViewportSize({ width: 393, height: 852 });
    await page.goto("/#/chat", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);

    const trigger = page.getByTestId("chat-org-trigger");
    await expect(trigger).toBeVisible({ timeout: 15_000 });
    await trigger.click();

    const menu = page.getByTestId("chat-org-menu");
    await expect(menu).toBeVisible();
    const box = await menu.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(393.5);
  } finally {
    await page.request.delete(`${apiBase}/api/v2/orgs/${orgId}`);
  }
});
