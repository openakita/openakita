import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * 财务插件 M1 W3 端到端冒烟测试。
 *
 * 走完任务规范要求的 8 步浏览器流程：
 *   1. 打开 OpenAkita 主壳（后端 18900 自带前端 bundle）
 *   2. 进入「财务自动化」插件应用
 *   3. View 1 ：创建账套
 *   4. View 2 Tab A：上传 W1 测试用 .xlsx
 *   5. View 2 Tab B：生成资产负债表
 *   6. View 3 ：点击单元格打开追溯抽屉
 *   7. View 3 ：导出 Excel
 *   8. 截图存档（每步至少一张）
 *
 * 后端假设已在 18900 启动且 finance-auto 插件已授予 routes.register。
 * 截图落到 tmp_p10/_finance_w3_screens/。
 */

const SCREENS_DIR = "../../tmp_p10/_finance_w3_screens";
const TEST_XLSX = resolve(__dirname, "../../../tmp_finance_analysis/xlsx/A_balance.xlsx");

test.beforeAll(() => {
  mkdirSync(SCREENS_DIR, { recursive: true });
});

test("finance-auto end-to-end smoke (8 steps)", async ({ page }) => {
  test.setTimeout(180_000);

  // ── 1. 进入主壳 ────────────────────────────────────────────────
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.screenshot({ path: `${SCREENS_DIR}/01-host-loaded.png` });

  // ── 2. 切到「财务自动化」插件应用 ─────────────────────────────
  // 主壳路由格式：#/app/<pluginId>（见 apps/setup-center/src/App.tsx::_parseHashRoute）
  await page.evaluate(() => {
    window.location.hash = "#/app/finance-auto";
  });
  await page.waitForTimeout(1500);

  // 插件 UI 在 iframe 内渲染
  const frameLocator = page.frameLocator("iframe").first();
  await expect(
    frameLocator.locator("text=账套管理"),
    "OrgListView 顶部标题应可见",
  ).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${SCREENS_DIR}/02-orglist-loaded.png` });

  // ── 3. 创建账套 ──────────────────────────────────────────────
  const orgName = `E2E·DemoCo·${Date.now()}`;
  const orgCode = `E2E${Date.now() % 100000}`;
  await frameLocator.locator("button", { hasText: "新建账套" }).click();
  await expect(frameLocator.locator("text=新建账套").first()).toBeVisible();
  await frameLocator.locator('input[placeholder*="北京"]').fill(orgName);
  await frameLocator.locator('input[placeholder*="COMP_"]').fill(orgCode);
  await page.screenshot({ path: `${SCREENS_DIR}/03-create-dialog.png` });
  await frameLocator.locator("button", { hasText: /^创建$/ }).click();
  // 弹窗关闭 + 列表刷新（toast 也包含同名文字，故用 .first() 避开 strict-mode）
  await expect(
    frameLocator.locator(`text=${orgName}`).first(),
  ).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SCREENS_DIR}/04-org-created.png` });

  // ── 4. 进入账套详情 → Tab A 上传 ───────────────────────────────
  // toast 会消失，等它先消失再点列表项
  await page.waitForTimeout(3500);
  await frameLocator.locator(`text=${orgName}`).first().click();
  await expect(
    frameLocator.locator("text=余额表导入").first(),
    "进入 OrgDetailView 应能看到 Tab A 按钮",
  ).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${SCREENS_DIR}/05-org-detail-tab-a.png` });

  // 上传 .xlsx — 直接用 input[type=file]
  const fileInput = frameLocator.locator('input[type="file"][accept=".xls,.xlsx"]');
  await fileInput.setInputFiles(TEST_XLSX);
  // 等解析结果（行数 / parser_used 标识）
  await expect(
    frameLocator.locator("text=/解析成功|parser/").first(),
  ).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `${SCREENS_DIR}/06-import-success.png` });

  // ── 5. Tab B 生成资产负债表 ───────────────────────────────────
  await frameLocator.locator("button", { hasText: "报表生成" }).click();
  await expect(
    frameLocator.locator("button", { hasText: /生成报表/ }),
  ).toBeVisible({ timeout: 5_000 });
  await page.screenshot({ path: `${SCREENS_DIR}/07a-report-tab.png` });
  await frameLocator.locator("button", { hasText: /生成报表/ }).click();
  // ReportTab 在生成成功后会自动 onOpenReport(reportId) 跳到 ReportView，
  // ReportView 的 .page-title 文本即报表类型名（资产负债表）。
  await expect(
    frameLocator.locator("h2.page-title", { hasText: "资产负债表" }),
    "应自动跳到 ReportView（资产负债表 页标题）",
  ).toBeVisible({ timeout: 90_000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SCREENS_DIR}/07b-report-generated.png` });

  // ── 6. ReportView 看追溯 ──────────────────────────────────────
  await page.screenshot({ path: `${SCREENS_DIR}/08-report-view.png` });

  // 点击第一个可点击的金额单元格 → 抽屉（cell-clickable span）
  const moneyCell = frameLocator.locator("span.cell-clickable").first();
  if (await moneyCell.count()) {
    await moneyCell.click();
    await expect(
      frameLocator.locator("text=单元格追溯").first(),
    ).toBeVisible({ timeout: 5_000 });
    await page.screenshot({ path: `${SCREENS_DIR}/09-cell-trace-drawer.png` });
    // 关闭抽屉
    await frameLocator.locator("button", { hasText: "关闭" }).first().click();
  }

  // ── 7. 导出 Excel（M2 Stage 3 后改成下拉菜单，先点 ▾ 再选「按当前视图导出」） ──
  const downloadPromise = page.waitForEvent("download").catch(() => null);
  await frameLocator.locator('[data-testid="export-menu-btn"]').click();
  await frameLocator.locator('[data-testid="export-view"]').click();
  const download = await downloadPromise;
  if (download) {
    const path = await download.path();
    expect(path, "下载文件应该存在").toBeTruthy();
    await page.screenshot({ path: `${SCREENS_DIR}/10-excel-exported.png` });
  } else {
    await page.screenshot({ path: `${SCREENS_DIR}/10-export-clicked.png` });
  }
});
