import { chromium } from "playwright";

const baseUrl = process.env.SETUP_CENTER_URL || "http://127.0.0.1:5173/web/";

const routes = [
  "#/chat",
  "#/status",
  "#/skills",
  "#/mcp",
  "#/plugins",
  "#/scheduler",
  "#/memory",
  "#/identity",
  "#/security",
  "#/token-stats",
];

const fatalPatterns = [
  /useContext/i,
  /A component suspended while responding to synchronous input/i,
  /removeChild/i,
  /Minified React error/i,
  /Cannot read properties of null/i,
];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const failures = [];

page.on("console", (msg) => {
  if (msg.type() !== "error") return;
  const text = msg.text();
  if (fatalPatterns.some((pattern) => pattern.test(text))) {
    failures.push(`console.error: ${text}`);
  }
});

page.on("pageerror", (err) => {
  const text = err?.stack || err?.message || String(err);
  if (fatalPatterns.some((pattern) => pattern.test(text))) {
    failures.push(`pageerror: ${text}`);
  }
});

for (const route of routes) {
  const url = new URL(baseUrl);
  url.hash = route;
  await page.goto(url.toString(), { waitUntil: "domcontentloaded", timeout: 15_000 });
  await page.waitForTimeout(750);
}

await browser.close();

if (failures.length) {
  console.error(`Route smoke failed with ${failures.length} fatal frontend error(s):`);
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log(`Route smoke passed for ${routes.length} hash routes at ${baseUrl}`);
