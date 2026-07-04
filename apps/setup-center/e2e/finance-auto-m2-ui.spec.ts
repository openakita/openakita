import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";

/**
 * 财务插件 M2 前端端到端冒烟测试。
 *
 * 与 W3 (finance-auto-ui.spec.ts) 不同，M2 测试**不依赖完整的后端余额表**
 * —— 后端 collab / consolidation / ai 路由有的尚未挂入主路由，所以本 spec
 * 把所有「网络层」操作都在 frame 内通过 `page.evaluate` mock 掉，专门验证：
 *
 *   1. M2 主导航 (账套 / 合并报表 / AI 设置) 都能切换。
 *   2. 用户切换器 dropdown 打开 + 角色徽章 + 「注册新用户」入口（admin）。
 *   3. AI consent 弹窗：直接 dispatch `mock_consent_request` event 触发 → 验证
 *      🔴/🟡/🟢 三个敏感度按钮 + 「拒绝/允许一次/永久允许」按钮齐全。
 *   4. 合并报表页：localStorage 注入 mock 集团 → 列表/详情可见。
 *   5. AI 设置页三 Tab 切换。
 *
 * 截图存档至 tmp_p10/_finance_m2_screens/。
 */

const SCREENS_DIR = "../../tmp_p10/_finance_m2_screens";

test.beforeAll(() => {
  mkdirSync(SCREENS_DIR, { recursive: true });
});

async function gotoFinancePlugin(page: import("@playwright/test").Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.evaluate(() => { window.location.hash = "#/app/finance-auto"; });
  await page.waitForTimeout(1500);
  return page.frameLocator("iframe").first();
}

test("M2 主导航 + 用户切换器 + AI 设置 Tab", async ({ page }) => {
  test.setTimeout(60_000);

  const frame = await gotoFinancePlugin(page);
  await expect(frame.locator('[data-testid="nav-orgs"]')).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${SCREENS_DIR}/01-mainnav-orgs.png` });

  // ── 用户切换器 ────────────────────────────────────────────────
  await frame.locator('[data-testid="user-switcher-btn"]').click();
  await expect(frame.locator('[data-testid="user-switcher-menu"]')).toBeVisible();
  await page.screenshot({ path: `${SCREENS_DIR}/02-user-switcher.png` });

  // 默认 admin → 应能看到「注册新用户…」按钮
  await expect(frame.locator('[data-testid="register-user"]')).toBeVisible();

  // 切到「演示·审计师 张三」
  await frame.locator('[data-testid="user-switcher-menu"] button', { hasText: "演示·审计师 张三" }).click();
  // 切换后下拉关闭，按钮里有「审计师」徽章
  await expect(frame.locator('[data-testid="user-switcher-btn"]', { hasText: /演示·审计师|审计师/ })).toBeVisible();
  await page.screenshot({ path: `${SCREENS_DIR}/03-user-switched-auditor.png` });

  // ── AI 设置 ─────────────────────────────────────────────────
  await frame.locator('[data-testid="nav-ai-settings"]').click();
  await expect(frame.locator('[data-testid="tab-ai-scenarios"]')).toBeVisible({ timeout: 5_000 });
  await expect(frame.locator('[data-testid="tab-ai-consent"]')).toBeVisible();
  await expect(frame.locator('[data-testid="tab-ai-audit"]')).toBeVisible();
  await page.screenshot({ path: `${SCREENS_DIR}/04-ai-settings-scenarios.png` });
  await frame.locator('[data-testid="tab-ai-consent"]').click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SCREENS_DIR}/05-ai-settings-consent.png` });
  await frame.locator('[data-testid="tab-ai-audit"]').click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SCREENS_DIR}/06-ai-settings-audit.png` });
});

test("M2 AI consent 弹窗（mock WS event）", async ({ page }) => {
  test.setTimeout(60_000);

  const frame = await gotoFinancePlugin(page);
  await expect(frame.locator('[data-testid="nav-orgs"]')).toBeVisible({ timeout: 15_000 });

  // 在 iframe 内 dispatch `mock_consent_request` 事件，AIConsentBridge 会消费该
  // 事件、走 mock 模式并展示 AIConsentDialog。
  await frame.locator("body").evaluate(() => {
    window.dispatchEvent(new CustomEvent("mock_consent_request"));
  });
  // 弹窗 title 与 sample.scenario_description 来自 buildSampleConsentRequest
  await expect(frame.locator("text=AI 数据使用授权").first()).toBeVisible({ timeout: 5_000 });
  await page.screenshot({ path: `${SCREENS_DIR}/07-consent-dialog.png` });

  // 三个决策按钮齐全（用 data-testid 更稳定）
  await expect(frame.locator('[data-testid="ai-consent-deny"]')).toBeVisible();
  await expect(frame.locator('[data-testid="ai-consent-allow-once"]')).toBeVisible();
  await expect(frame.locator('[data-testid="ai-consent-allow-perm"]')).toBeVisible();
  await page.screenshot({ path: `${SCREENS_DIR}/08-consent-decisions.png` });

  // 点「仅本次允许」（mock 模式下 POST /ai/consent/respond 会 404，但前端会容错）
  await frame.locator('[data-testid="ai-consent-allow-once"]').click();
  // 弹窗关闭
  await page.waitForTimeout(800);
  await expect(frame.locator("text=AI 数据使用授权").first()).toHaveCount(0);
  await page.screenshot({ path: `${SCREENS_DIR}/09-consent-dismissed.png` });
});

test("M2 合并报表 ConsolidationView (mock localStorage)", async ({ page }) => {
  test.setTimeout(60_000);

  const frame = await gotoFinancePlugin(page);
  await expect(frame.locator('[data-testid="nav-orgs"]')).toBeVisible({ timeout: 15_000 });

  // 注入 mock 集团到 localStorage（key 与 ConsolidationListView 一致）
  await frame.locator("body").evaluate(() => {
    const store = {
      _nextId: 99,
      list: [
        {
          group_id: 1, name: "集团 ABC（mock）", parent_org_id: "org_aaaaaaaa",
          description: "Playwright 测试集团",
          created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
          _members_count: 2, version: 1, created_by: "local",
        },
      ],
      groups: {
        1: {
          members: [
            { id: 1, group_id: 1, subsidiary_org_id: "org_aaaaaaaa", ownership_pct: 100, join_method: "full", is_parent: true, added_at: new Date().toISOString(), version: 1 },
            { id: 2, group_id: 1, subsidiary_org_id: "org_bbbbbbbb", ownership_pct:  60, join_method: "full", is_parent: false, added_at: new Date().toISOString(), version: 1 },
          ],
          eliminations: [],
          runs: [],
        },
      },
    };
    localStorage.setItem("finance.consolidation.groups.v1", JSON.stringify(store));
  });

  // 进入合并报表
  await frame.locator('[data-testid="nav-consolidation"]').click();
  await expect(frame.locator("text=集团 ABC（mock）").first()).toBeVisible({ timeout: 5_000 });
  await page.screenshot({ path: `${SCREENS_DIR}/10-consolidation-list.png` });

  // 打开集团详情
  await frame.locator("text=集团 ABC（mock）").first().click();
  await expect(frame.locator('[data-testid="tab-members"]')).toBeVisible({ timeout: 5_000 });
  await expect(frame.locator('[data-testid="tab-elim"]')).toBeVisible();
  await expect(frame.locator('[data-testid="tab-runs"]')).toBeVisible();
  await page.screenshot({ path: `${SCREENS_DIR}/11-consolidation-detail-members.png` });

  // 切到 Runs Tab → 触发合并（mock）
  await frame.locator('[data-testid="tab-runs"]').click();
  await frame.locator('[data-testid="trigger-run"]').click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SCREENS_DIR}/12-consolidation-run-mock.png` });
});
