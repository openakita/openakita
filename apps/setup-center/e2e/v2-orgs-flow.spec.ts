import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";

/**
 * smoke-5bug end-to-end sweep.
 *
 * Validates the four user-facing fixes in this batch (B5 / B3 / B1 /
 * B2) against the running dev stack (Vite on :5173, OpenAkita backend
 * on :18900).  Each step is independently asserted; failure messages
 * include the bug id so the final report can map verdicts back to
 * commits.  Screenshots land under ``tmp_p10/_5bug_screens/``.
 */

const SCREENS_DIR = "../../tmp_p10/_5bug_screens";

test.beforeAll(() => {
  mkdirSync(SCREENS_DIR, { recursive: true });
});

/** Helper -- the empty-state trigger fires before any org is selected,
 *  the compact trigger appears once an org-list item is highlighted.
 *  Either path opens the same TemplatePickerDialog. */
function templateTrigger(page: import("@playwright/test").Page) {
  return page
    .locator(
      '[data-testid="org-editor-v2-template-trigger"], [data-testid="org-editor-v2-template-trigger-compact"]',
    )
    .getByRole("button")
    .first();
}

test("smoke-5bug full v2 orgs flow", async ({ page }) => {
  const apiBase = "http://127.0.0.1:18900";

  // -- 1. Open the SPA and navigate to the Org Editor via hash routing.
  await page.goto("/#org-editor", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.screenshot({ path: `${SCREENS_DIR}/01-org-editor-loaded.png`, fullPage: false });

  // -- 2. Open the TemplatePickerDialog.
  const trigger = templateTrigger(page);
  await expect(trigger, "B2 sidebar trigger should be visible (not clipped)").toBeVisible({ timeout: 15000 });
  await trigger.click();
  const dialog = page.getByTestId("v2-template-dialog");
  await expect(dialog, "B1 dialog should mount on trigger click").toBeVisible({ timeout: 10000 });

  // -- 3 (B1). Bounding box within 10% of viewport center.
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SCREENS_DIR}/02-modal-opened.png`, fullPage: false });
  const viewport = page.viewportSize()!;
  const box = await dialog.boundingBox();
  expect(box, "B1 dialog should have a bounding box").not.toBeNull();
  const cx = (box!.x + box!.width / 2) / viewport.width;
  const cy = (box!.y + box!.height / 2) / viewport.height;
  expect(cx, `B1 dialog center-X should be near 50% of viewport (got ${(cx * 100).toFixed(1)}%)`).toBeGreaterThan(0.4);
  expect(cx).toBeLessThan(0.6);
  expect(cy, `B1 dialog center-Y should be near 50% of viewport (got ${(cy * 100).toFixed(1)}%)`).toBeGreaterThan(0.4);
  expect(cy).toBeLessThan(0.6);

  // -- 4. Pick first template card.
  const cards = page.getByTestId("v2-template-dialog-list").locator("li");
  await expect(cards.first(), "B1 templates should load").toBeVisible({ timeout: 10000 });
  await cards.first().click();

  // -- 5. Type name + create.
  const orgName = `smoke-e2e-${Date.now()}`;
  await page.locator("#tpd-org-name").fill(orgName);
  const createBtn = page.getByTestId("v2-template-dialog-create");
  await expect(createBtn).toBeEnabled();
  await createBtn.click();

  // -- 6. Modal closes.
  await expect(dialog).toBeHidden({ timeout: 15000 });
  await page.screenshot({ path: `${SCREENS_DIR}/03-after-create.png`, fullPage: false });

  // Find the new org id via the list endpoint.
  let orgId = "";
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const list = await page.request.get(`${apiBase}/api/v2/orgs`);
    const arr = (await list.json()) as Array<{ id: string; name: string }>;
    const hit = arr.find((o) => o.name === orgName);
    if (hit) {
      orgId = hit.id;
      break;
    }
    await page.waitForTimeout(800);
  }
  expect(orgId, `created org "${orgName}" should be discoverable via list endpoint`).not.toBe("");

  // -- 7 (B5). Drive POST /start -- same wire path the broken "启动" button hit.
  const startResp = await page.request.post(`${apiBase}/api/v2/orgs/${orgId}/start`);
  expect(startResp.status(), "B5 POST /start must NOT 503").toBe(200);
  const startBody = (await startResp.json()) as { status: string; ok: boolean };
  expect(startBody.ok, "B5 start_org must succeed").toBe(true);
  expect(startBody.status.toUpperCase(), "B5 status should transition to ACTIVE").toBe("ACTIVE");

  // -- 8 (B3). Full PUT body round-trip.
  const fullBody = {
    name: orgName,
    description: "smoke e2e",
    user_persona: { title: "User", display_name: "U", description: "" },
    operation_mode: "command",
    core_business: "",
    layout_locked: false,
    workspace_dir: "",
    auto_persist_final_answer: true,
    watchdog_enabled: true,
    watchdog_interval_s: 30,
    watchdog_stuck_threshold_s: 1800,
    watchdog_silence_threshold_s: 1800,
    heartbeat_enabled: false,
    heartbeat_interval_s: 600,
    standup_enabled: false,
    nodes: [],
    edges: [],
  };
  const putResp = await page.request.put(`${apiBase}/api/v2/orgs/${orgId}`, {
    data: fullBody,
    headers: { "Content-Type": "application/json" },
  });
  expect(putResp.status(), "B3 PUT with 17-key body must NOT 422").toBe(200);

  // -- 9. Cleanup.
  await page.request.delete(`${apiBase}/api/v2/orgs/${orgId}`);
});

test("smoke-5-sse mint-runtime org exposes /events and /stream", async ({ page }) => {
  const apiBase = "http://127.0.0.1:18900";

  // 1. Pick the first template + create a fresh mint org via the API.
  const tplResp = await page.request.get(`${apiBase}/api/v2/orgs/templates`);
  expect(tplResp.status(), "templates endpoint should respond 200").toBe(200);
  const templates = (await tplResp.json()) as Array<{ id: string }>;
  expect(templates.length, "at least one builtin template").toBeGreaterThan(0);
  const orgName = `smoke-sse-${Date.now()}`;
  const createResp = await page.request.post(
    `${apiBase}/api/v2/orgs/from-template`,
    {
      data: { template_id: templates[0].id, name: orgName },
      headers: { "Content-Type": "application/json" },
    },
  );
  expect(createResp.status(), "from-template must mint the org").toBe(201);
  const orgId = ((await createResp.json()) as { id: string }).id;
  expect(orgId, "mint runtime returns an id").toMatch(/^org_/);

  try {
    // 2. /events must now resolve (pre-fix it 404'd "Event store not found").
    const evResp = await page.request.get(`${apiBase}/api/v2/orgs/${orgId}/events?limit=5`);
    expect(evResp.status(), "smoke-5-sse: /events must NOT 404 for mint orgs").toBe(200);
    const evBody = await evResp.json();
    expect(Array.isArray(evBody), "/events returns a JSON list").toBe(true);

    // 3. /stream must open as SSE and deliver the initial sse_connected event.
    //    We poke EventSource from the page context so the Playwright network
    //    stack handles streaming correctly (page.request would read to EOF).
    await page.goto("/#org-editor", { waitUntil: "domcontentloaded" });
    const sseResult = await page.evaluate(
      ({ url, timeoutMs }) =>
        new Promise<{ ok: boolean; readyState: number; event?: unknown; error?: string }>((resolve) => {
          const es = new EventSource(url);
          const timer = window.setTimeout(() => {
            es.close();
            resolve({ ok: false, readyState: es.readyState, error: "timeout" });
          }, timeoutMs);
          es.addEventListener("lifecycle", (ev) => {
            window.clearTimeout(timer);
            const parsed = (() => {
              try {
                return JSON.parse((ev as MessageEvent).data);
              } catch {
                return null;
              }
            })();
            es.close();
            resolve({ ok: true, readyState: es.readyState, event: parsed });
          });
          es.addEventListener("error", () => {
            // EventSource fires error on initial 404; bail fast.
            if (es.readyState === 2) {
              window.clearTimeout(timer);
              resolve({ ok: false, readyState: es.readyState, error: "closed" });
            }
          });
        }),
      { url: `${apiBase}/api/v2/orgs/${orgId}/stream`, timeoutMs: 8000 },
    );
    expect(sseResult.ok, `smoke-5-sse: EventSource never received first event (${JSON.stringify(sseResult)})`).toBe(true);
    expect(sseResult.event, "first SSE event payload must include org_id + type").toEqual(
      expect.objectContaining({ org_id: orgId, type: "sse_connected" }),
    );
  } finally {
    await page.request.delete(`${apiBase}/api/v2/orgs/${orgId}`);
  }
});

test("smoke-5bug B2 sidebar toolbar wraps cleanly at 1024x768", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/#org-editor", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => undefined);
  const trigger = templateTrigger(page);
  await expect(trigger, "B2 trigger should be visible at 1024x768").toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: `${SCREENS_DIR}/04-sidebar-1024x768.png`, fullPage: false });
});

test("smoke-5bug B1 modal centering at 800x600", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 600 });
  await page.goto("/#org-editor", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => undefined);
  const trigger = templateTrigger(page);
  await expect(trigger).toBeVisible({ timeout: 15000 });
  await trigger.click();
  const dialog = page.getByTestId("v2-template-dialog");
  await expect(dialog).toBeVisible({ timeout: 10000 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SCREENS_DIR}/05-modal-800x600.png`, fullPage: false });
  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  const cx = (box!.x + box!.width / 2) / 800;
  const cy = (box!.y + box!.height / 2) / 600;
  expect(cx, `B1@800x600 cx=${(cx * 100).toFixed(1)}%`).toBeGreaterThan(0.4);
  expect(cx).toBeLessThan(0.6);
  expect(cy, `B1@800x600 cy=${(cy * 100).toFixed(1)}%`).toBeGreaterThan(0.4);
  expect(cy).toBeLessThan(0.6);
});

test("smoke-5bug B1 modal centering at 1920x1080", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/#org-editor", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => undefined);
  const trigger = templateTrigger(page);
  await expect(trigger).toBeVisible({ timeout: 15000 });
  await trigger.click();
  const dialog = page.getByTestId("v2-template-dialog");
  await expect(dialog).toBeVisible({ timeout: 10000 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SCREENS_DIR}/06-modal-1920x1080.png`, fullPage: false });
  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  const cx = (box!.x + box!.width / 2) / 1920;
  const cy = (box!.y + box!.height / 2) / 1080;
  expect(cx, `B1@1920x1080 cx=${(cx * 100).toFixed(1)}%`).toBeGreaterThan(0.4);
  expect(cx).toBeLessThan(0.6);
  expect(cy, `B1@1920x1080 cy=${(cy * 100).toFixed(1)}%`).toBeGreaterThan(0.4);
  expect(cy).toBeLessThan(0.6);
});