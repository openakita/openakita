/**
 * Feedback Worker — Cloudflare Workers + R2 + KV
 *
 * Handles both bug reports and feature requests.
 *
 * Endpoints:
 *   PUT  /report/:id         — Submit a report (Turnstile + IP rate-limit)
 *   GET  /admin/reports       — List reports (Admin API Key, ?type=bug|feature)
 *   GET  /admin/reports/:id   — Report metadata (Admin API Key)
 *   GET  /admin/reports/:id/download — Download zip (Admin API Key)
 *   DELETE /admin/reports/:id — Delete report (Admin API Key)
 */

export interface Env {
  REPORTS: R2Bucket;
  RATE_LIMIT: KVNamespace;
  TURNSTILE_SECRET_KEY: string;
  ADMIN_API_KEY: string;
  RESEND_API_KEY: string;
  NOTIFY_EMAIL: string;
}

const MAX_REPORT_SIZE = 30 * 1024 * 1024; // 30 MB
const IP_DAILY_LIMIT = 10;
const GLOBAL_DAILY_LIMIT = 1000;
const KV_TTL = 86400 * 2; // 2 days

// ─── Helpers ──────────────────────────────────────────────────

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function clientIP(request: Request): string {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

function corsHeaders(): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "*",
  };
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders() },
  });
}

function error(msg: string, status: number): Response {
  return json({ error: msg }, status);
}

function safeDecodeURI(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

// ─── Turnstile Verification ──────────────────────────────────

async function verifyTurnstile(
  token: string,
  ip: string,
  secret: string,
): Promise<boolean> {
  const res = await fetch(
    "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secret, response: token, remoteip: ip }),
    },
  );
  const data = (await res.json()) as { success: boolean };
  return data.success === true;
}

// ─── Rate Limiting (KV) ─────────────────────────────────────

async function checkRateLimit(
  kv: KVNamespace,
  ip: string,
): Promise<string | null> {
  const d = today();
  const checks = [
    { key: `rl:ip:${ip}:${d}`, limit: IP_DAILY_LIMIT, msg: "IP daily limit reached" },
    { key: `rl:global:${d}`, limit: GLOBAL_DAILY_LIMIT, msg: "Global daily limit reached" },
  ];

  for (const { key, limit, msg } of checks) {
    const count = parseInt((await kv.get(key)) || "0");
    if (count >= limit) return msg;
  }

  for (const { key } of checks) {
    const count = parseInt((await kv.get(key)) || "0");
    await kv.put(key, String(count + 1), { expirationTtl: KV_TTL });
  }

  return null;
}

// ─── Email Notification ─────────────────────────────────────

async function sendNotification(
  env: Env,
  reportId: string,
  title: string,
  summary: string,
  typeLabel = "Bug Report",
): Promise<void> {
  if (!env.RESEND_API_KEY || !env.NOTIFY_EMAIL) return;

  const truncatedSummary =
    summary.length > 800 ? summary.slice(0, 800) + "..." : summary;

  try {
    await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
      },
      body: JSON.stringify({
        from: "OpenAkita Feedback <onboarding@resend.dev>",
        to: [env.NOTIFY_EMAIL],
        subject: `[${typeLabel}] ${title}`,
        html: `
          <h2>${escapeHtml(typeLabel)}: ${escapeHtml(title)}</h2>
          <p><strong>Report ID:</strong> ${reportId}</p>
          <p><strong>Time:</strong> ${new Date().toISOString()}</p>
          <hr/>
          <pre style="white-space:pre-wrap;font-size:13px;">${escapeHtml(truncatedSummary)}</pre>
          <hr/>
          <p style="color:#888;font-size:12px;">
            Use admin API to download the full report zip:<br/>
            <code>GET /admin/reports/${reportId}/download</code>
          </p>
        `,
      }),
    });
  } catch {
    // Email failure is non-critical
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─── Admin Auth ─────────────────────────────────────────────

function isAdmin(request: Request, env: Env): boolean {
  const auth = request.headers.get("Authorization") || "";
  return auth === `Bearer ${env.ADMIN_API_KEY}`;
}

// ─── Route: PUT /report/:id ─────────────────────────────────

async function handleSubmit(
  request: Request,
  reportId: string,
  env: Env,
): Promise<Response> {
  // 1. Validate content length
  const contentLength = parseInt(request.headers.get("Content-Length") || "0");
  if (contentLength > MAX_REPORT_SIZE) {
    return error("Report too large (max 30MB)", 413);
  }

  // 2. Verify Turnstile token
  const turnstileToken = request.headers.get("X-Turnstile-Token") || "";
  if (!turnstileToken) {
    return error("Missing Turnstile token", 403);
  }
  const ip = clientIP(request);
  const valid = await verifyTurnstile(turnstileToken, ip, env.TURNSTILE_SECRET_KEY);
  if (!valid) {
    return error("Turnstile verification failed", 403);
  }

  // 3. Rate limiting
  const rateLimitMsg = await checkRateLimit(env.RATE_LIMIT, ip);
  if (rateLimitMsg) {
    return error(rateLimitMsg, 429);
  }

  // 4. Validate required headers (URL-encoded for non-ASCII support)
  const titleRaw = request.headers.get("X-Report-Title") || "";
  const title = safeDecodeURI(titleRaw);
  if (title.length < 2 || title.length > 200) {
    return error("Title must be 2-200 characters", 400);
  }

  // 5. Read body and store in R2
  const body = await request.arrayBuffer();
  if (body.byteLength === 0) {
    return error("Empty report body", 400);
  }

  const reportType = request.headers.get("X-Report-Type") || "bug";
  const summary = safeDecodeURI(request.headers.get("X-Report-Summary") || "");
  const extraInfo = safeDecodeURI(request.headers.get("X-Report-System-Info") || "");
  const metadata = {
    id: reportId,
    type: reportType,
    title,
    summary: summary.slice(0, 2000),
    extra_info: extraInfo.slice(0, 2000),
    ip: ip,
    created_at: new Date().toISOString(),
    size_bytes: body.byteLength,
  };

  await env.REPORTS.put(`reports/${reportId}/report.zip`, body, {
    customMetadata: { title, type: reportType, created_at: metadata.created_at },
  });

  await env.REPORTS.put(
    `reports/${reportId}/metadata.json`,
    JSON.stringify(metadata, null, 2),
    { httpMetadata: { contentType: "application/json" } },
  );

  // 6. Send email notification (non-blocking)
  const typeLabel = reportType === "feature" ? "Feature Request" : "Bug Report";
  const infoLabel = reportType === "feature" ? "Contact" : "System Info";
  const emailBody = summary
    ? `${summary}\n\n--- ${infoLabel} ---\n${extraInfo}`
    : `(No description)\n\n--- ${infoLabel} ---\n${extraInfo}`;
  await sendNotification(env, reportId, title, emailBody, typeLabel);

  return json({ status: "ok", report_id: reportId });
}

// ─── Route: GET /admin/reports ──────────────────────────────

async function handleListReports(
  request: Request,
  env: Env,
): Promise<Response> {
  if (!isAdmin(request, env)) return error("Unauthorized", 401);

  const url = new URL(request.url);
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "50"), 100);
  const cursor = url.searchParams.get("cursor") || undefined;
  const typeFilter = url.searchParams.get("type") || ""; // "bug" | "feature" | "" (all)

  const listed = await env.REPORTS.list({
    prefix: "reports/",
    delimiter: "/",
    cursor,
    limit: typeFilter ? limit * 3 : limit, // over-fetch when filtering
  });

  // Each "common prefix" is a report directory like "reports/{id}/"
  const reportDirs = (listed.delimitedPrefixes || []).map((p: string) => {
    const id = p.replace("reports/", "").replace("/", "");
    return id;
  });

  // Fetch metadata for each report
  let reports = await Promise.all(
    reportDirs.map(async (id: string) => {
      const metaObj = await env.REPORTS.get(`reports/${id}/metadata.json`);
      if (!metaObj) return { id, type: "bug", title: "(unknown)", created_at: null };
      try {
        return JSON.parse(await metaObj.text());
      } catch {
        return { id, type: "bug", title: "(parse error)", created_at: null };
      }
    }),
  );

  if (typeFilter) {
    reports = reports.filter((r: any) => r.type === typeFilter);
    reports = reports.slice(0, limit);
  }

  return json({
    reports,
    total: reports.length,
    truncated: listed.truncated,
    cursor: listed.truncated ? listed.cursor : null,
  });
}

// ─── Route: GET /admin/reports/:id ──────────────────────────

async function handleGetReport(
  request: Request,
  reportId: string,
  env: Env,
): Promise<Response> {
  if (!isAdmin(request, env)) return error("Unauthorized", 401);

  const metaObj = await env.REPORTS.get(`reports/${reportId}/metadata.json`);
  if (!metaObj) return error("Report not found", 404);

  const metadata = JSON.parse(await metaObj.text());
  return json(metadata);
}

// ─── Route: GET /admin/reports/:id/download ─────────────────

async function handleDownloadReport(
  request: Request,
  reportId: string,
  env: Env,
): Promise<Response> {
  if (!isAdmin(request, env)) return error("Unauthorized", 401);

  const obj = await env.REPORTS.get(`reports/${reportId}/report.zip`);
  if (!obj) return error("Report not found", 404);

  return new Response(obj.body, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="report_${reportId}.zip"`,
      ...corsHeaders(),
    },
  });
}

// ─── Route: DELETE /admin/reports/:id ────────────────────────

async function handleDeleteReport(
  request: Request,
  reportId: string,
  env: Env,
): Promise<Response> {
  if (!isAdmin(request, env)) return error("Unauthorized", 401);

  await env.REPORTS.delete(`reports/${reportId}/report.zip`);
  await env.REPORTS.delete(`reports/${reportId}/metadata.json`);

  return json({ status: "ok", deleted: reportId });
}

// ─── Router ─────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    // PUT /report/:id
    const submitMatch = path.match(/^\/report\/([a-zA-Z0-9_-]+)$/);
    if (submitMatch && request.method === "PUT") {
      return handleSubmit(request, submitMatch[1], env);
    }

    // GET /admin/reports
    if (path === "/admin/reports" && request.method === "GET") {
      return handleListReports(request, env);
    }

    // GET /admin/reports/:id
    const reportMatch = path.match(/^\/admin\/reports\/([a-zA-Z0-9_-]+)$/);
    if (reportMatch && request.method === "GET") {
      return handleGetReport(request, reportMatch[1], env);
    }

    // GET /admin/reports/:id/download
    const downloadMatch = path.match(
      /^\/admin\/reports\/([a-zA-Z0-9_-]+)\/download$/,
    );
    if (downloadMatch && request.method === "GET") {
      return handleDownloadReport(request, downloadMatch[1], env);
    }

    // DELETE /admin/reports/:id
    if (reportMatch && request.method === "DELETE") {
      return handleDeleteReport(request, reportMatch[1], env);
    }

    // Health check
    if (path === "/" || path === "/health") {
      return json({ status: "ok", service: "feedback-worker" });
    }

    return error("Not found", 404);
  },
};

