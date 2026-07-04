#!/usr/bin/env node
/**
 * gen-types.mjs — generate TypeScript type definitions for the
 * finance-auto plugin REST surface by pulling the running OpenAkita
 * server's OpenAPI schema and feeding it to `openapi-typescript`.
 *
 * Closes audit EX-P2-12 from _finance_plugin_audit_extended_report.md
 * (frontend has no shared TS contract today, so backend field renames
 * silently break the iframe bundle at runtime).
 *
 * Usage:
 *   # Make sure the backend is running on http://127.0.0.1:18900
 *   # (or pass --base-url=).
 *   node plugins/finance-auto/ui/scripts/gen-types.mjs
 *
 * Optional flags:
 *   --base-url=<url>   Override OpenAkita base URL (default
 *                      http://127.0.0.1:18900)
 *   --out=<path>       Override output path (default
 *                      plugins/finance-auto/ui/dist/types/finance-auto-api.d.ts)
 *   --filter=<prefix>  Keep only paths starting with prefix (default
 *                      /api/plugins/finance-auto)
 *
 * NOT wired into CI for v1.0 RC — the generated types are advisory
 * for the bundle author (iframe is plain JS today) and a future
 * refactor toward a real Vite build will consume them. See
 * _finance_plugin_audit_extended_report.md §9 (v1.0.x backlog).
 *
 * Why this lives in plugins/finance-auto/ui/scripts/ rather than
 * setup-center: the type file is for the plugin bundle, which has
 * its own iframe scope; setup-center already has its own OpenAPI
 * tooling against the host API surface.
 */

import { writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ---- arg parsing (minimal, no extra deps) -------------------------
const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const m = a.match(/^--([^=]+)=(.*)$/);
    return m ? [m[1], m[2]] : [a.replace(/^--/, ""), true];
  }),
);

const BASE_URL = args["base-url"] || "http://127.0.0.1:18900";
const FILTER = args.filter || "/api/plugins/finance-auto";
const OUT = args.out
  ? resolve(args.out)
  : resolve(__dirname, "..", "dist", "types", "finance-auto-api.d.ts");

// ---- helpers ------------------------------------------------------
function log(msg) {
  process.stdout.write(`[gen-types] ${msg}\n`);
}

function die(msg, code = 1) {
  process.stderr.write(`[gen-types] FATAL: ${msg}\n`);
  process.exit(code);
}

async function loadOpenApiTs() {
  // openapi-typescript may not be installed if the operator skipped
  // `npm install` in plugins/finance-auto/ui/. Detect that and fail
  // with a clear pointer rather than a confusing import error.
  try {
    const mod = await import("openapi-typescript");
    return mod.default || mod;
  } catch (err) {
    die(
      `openapi-typescript is not installed.\n` +
      `Run: cd plugins/finance-auto/ui && npm install\n` +
      `Underlying error: ${err.message}`,
    );
  }
}

// ---- main ---------------------------------------------------------
async function main() {
  log(`fetching ${BASE_URL}/openapi.json`);
  let schema;
  try {
    const resp = await fetch(`${BASE_URL}/openapi.json`);
    if (!resp.ok) {
      die(`HTTP ${resp.status} from ${BASE_URL}/openapi.json`);
    }
    schema = await resp.json();
  } catch (err) {
    die(
      `cannot reach ${BASE_URL}/openapi.json (${err.message})\n` +
      `Hint: start OpenAkita with \`openakita serve\` then retry.`,
    );
  }

  // Filter to the finance-auto subset so the generated .d.ts is
  // bounded; the full host schema is ~hundreds of paths.
  const filteredPaths = {};
  for (const [p, def] of Object.entries(schema.paths || {})) {
    if (p.startsWith(FILTER)) filteredPaths[p] = def;
  }
  const kept = Object.keys(filteredPaths).length;
  log(`kept ${kept} paths with prefix ${FILTER}`);

  if (kept === 0) {
    die(
      `no paths matched filter ${FILTER} — is the plugin loaded?\n` +
      `Verify with: curl ${BASE_URL}${FILTER}/health`,
    );
  }

  const filteredSchema = { ...schema, paths: filteredPaths };

  const openapiTS = await loadOpenApiTs();
  const ast = await openapiTS(filteredSchema, {
    transform(schemaObject) {
      // No-op transformer placeholder; v1.x can add e.g. Brand types
      // for org_id / period_id.
      return undefined;
    },
  });

  // openapi-typescript v7+ returns an AST; convert via printer.
  let printed;
  if (typeof ast === "string") {
    printed = ast;
  } else {
    const ts = await import("typescript");
    const tsApi = ts.default || ts;
    const printer = tsApi.createPrinter({ newLine: tsApi.NewLineKind.LineFeed });
    const sourceFile = tsApi.createSourceFile(
      "finance-auto-api.d.ts",
      "",
      tsApi.ScriptTarget.Latest,
      false,
      tsApi.ScriptKind.TS,
    );
    printed = printer.printList(tsApi.ListFormat.MultiLine, ast, sourceFile);
  }

  await mkdir(dirname(OUT), { recursive: true });
  const header = [
    "/* eslint-disable */",
    "/**",
    " * finance-auto API contract — AUTO-GENERATED by ui/scripts/gen-types.mjs",
    ` * Generated: ${new Date().toISOString()}`,
    ` * Source: ${BASE_URL}/openapi.json (paths starting with ${FILTER})`,
    " * DO NOT EDIT MANUALLY. Re-run:",
    " *   node plugins/finance-auto/ui/scripts/gen-types.mjs",
    " *",
    " * Closes audit EX-P2-12 from",
    " * _finance_plugin_audit_extended_report.md.",
    " */",
    "",
  ].join("\n");

  await writeFile(OUT, header + printed, "utf8");
  log(`wrote ${OUT}`);
  log(`done.`);
}

main().catch((err) => die(`unexpected: ${err.stack || err.message || err}`));
