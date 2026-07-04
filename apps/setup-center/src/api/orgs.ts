// NOTE: This file currently only talks to runtime endpoints
// (`/api/v2/orgs/...`). The spec endpoints (`/api/v2/orgs-spec/...`)
// still return a `{ "templates": [...] }` envelope and will be
// unified to bare arrays in P9.7gamma. See
// `docs/follow-ups/skipped-items-roadmap.md` §A.4 before touching
// the template APIs here — the frontend should continue to assume
// the runtime endpoint shape (bare array) as the canonical contract.

/**
 * V2 organisation API client wrappers (P9.8gamma smoke-blocker fix).
 *
 * Routes the v2 org create + template flow against the **mint
 * runtime** endpoints in
 * ``src/openakita/api/routes/orgs_v2_runtime_orgs.py`` (B1-B12 of the
 * P9.7 inventory) -- backed by ``runtime/orgs.OrgManager``, the same
 * store the sidebar ``GET /api/v2/orgs`` reads from. The older
 * Group A spec sub-app (``orgs-spec``) at the parallel namespace (a separate
 * ``JsonOrgStore``) is intentionally *not* used here: orgs landed
 * there via the previous ``listTemplates`` / ``instantiateTemplate``
 * / ``createOrg`` (orgs-spec) chain were invisible to the sidebar.
 * The only legitimate Group A caller in the frontend is the SSE
 * stream client at ``api/v2Stream.ts`` -- see sentinel #8 allowlist.
 *
 * Endpoints covered:
 *   GET    /api/v2/orgs/templates                       listTemplates
 *   GET    /api/v2/orgs/templates/{id}                  getTemplate
 *   POST   /api/v2/orgs/from-template                   instantiateTemplate (B8 instantiate+persist)
 *   GET    /api/v2/orgs                                 listOrgs
 *   GET    /api/v2/orgs/{id}                            getOrg
 *   PATCH  /api/v2/orgs/{id}                            patchOrg
 *   DELETE /api/v2/orgs/{id}                            deleteOrg
 */

import { apiGet, apiPost, apiPostRaw } from "../api";
import { apiUrl } from "../platform/apiUrl";
import { safeFetch } from "../providers";

// ---------------------------------------------------------------------------
// Wire types -- kept loose on purpose. The backend ships ``to_dict``
// snapshots that may grow new optional fields without breaking older
// frontends.
// ---------------------------------------------------------------------------

export interface TemplateNodeWire {
  id: string;
  type?: string;
  role?: string;
  label?: string;
  persona_prompt?: string | null;
  parent_id?: string | null;
  [key: string]: unknown;
}

export interface TemplateEdgeWire {
  id: string;
  org_id?: string;
  src?: string;
  dst?: string;
  kind?: string;
  [key: string]: unknown;
}

export interface TemplateWire {
  /** Template-summary shape returned by ``GET /api/v2/orgs/templates``. */
  id: string;
  name: string;
  display_name?: string;
  description?: string | null;
  icon?: string;
  node_count: number;
  tags?: string[];
  /** Present only on the full-detail ``GET /api/v2/orgs/templates/{id}`` response. */
  nodes?: TemplateNodeWire[];
  edges?: TemplateEdgeWire[];
  [key: string]: unknown;
}

export interface OrgWire {
  id: string;
  name: string;
  template_id?: string | null;
  description?: string | null;
  status: string;
  nodes: TemplateNodeWire[];
  edges: TemplateEdgeWire[];
  created_at: string;
  updated_at: string;
  [key: string]: unknown;
}

export interface InstantiateBody {
  /** New org name. Forwarded to mint runtime as a ``create_from_template`` override. */
  name: string;
  description?: string | null;
  /** Further overrides forwarded verbatim to ``OrgManager.create_from_template``. */
  [key: string]: unknown;
}

export interface PatchOrgBody {
  name?: string;
  description?: string | null;
}

// ---------------------------------------------------------------------------
// Templates (mint runtime: ``/api/v2/orgs/templates*``)
// ---------------------------------------------------------------------------

export function listTemplates(apiBase: string): Promise<TemplateWire[]> {
  return apiGet<TemplateWire[]>(apiUrl(apiBase, "api", "v2", "orgs", "templates"));
}

export function getTemplate(apiBase: string, templateId: string): Promise<TemplateWire> {
  return apiGet<TemplateWire>(apiUrl(apiBase, "api", "v2", "orgs", "templates", templateId));
}

export function instantiateTemplate(
  apiBase: string,
  templateId: string,
  body: InstantiateBody,
): Promise<OrgWire> {
  // P9.8gamma fix: the mint runtime collapses orgs-spec's
  // ``instantiate`` + ``persist`` into a single B8 call. Posting to
  // ``/from-template`` (mint) returns a 201 with the already-persisted
  // org, so the drawer no longer needs a follow-up ``createOrg`` POST.
  return apiPost<OrgWire>(apiUrl(apiBase, "api", "v2", "orgs", "from-template"), {
    template_id: templateId,
    ...body,
  });
}

// ---------------------------------------------------------------------------
// Orgs CRUD (mint runtime: ``/api/v2/orgs*``)
// ---------------------------------------------------------------------------

export function listOrgs(apiBase: string): Promise<OrgWire[]> {
  return apiGet<OrgWire[]>(apiUrl(apiBase, "api", "v2", "orgs"));
}

export function getOrg(apiBase: string, orgId: string): Promise<OrgWire> {
  return apiGet<OrgWire>(apiUrl(apiBase, "api", "v2", "orgs", orgId));
}

export async function patchOrg(
  apiBase: string,
  orgId: string,
  body: PatchOrgBody,
): Promise<OrgWire> {
  const res = await safeFetch(apiUrl(apiBase, "api", "v2", "orgs", orgId), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json() as Promise<OrgWire>;
}

export async function deleteOrg(apiBase: string, orgId: string): Promise<void> {
  await safeFetch(apiUrl(apiBase, "api", "v2", "orgs", orgId), { method: "DELETE" });
}

// Re-export apiPostRaw so callers can opt into low-level error inspection
// (e.g. show 404 from the disabled-v2 case as a UI hint).
export { apiPostRaw };
