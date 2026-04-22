# Skill: local-sd-flux

> **When to invoke me**
> The user wants to generate one or more images locally through their
> own ComfyUI server (Stable Diffusion 1.5 / SDXL / FLUX), or asks me
> to "rank which image backend to use" between local-GPU / local-CPU
> / a remote ComfyUI host.  I am *not* the right skill when the user
> explicitly wants a hosted vendor (DashScope / Tongyi / SDXL on
> Replicate); use those vendor-specific plugins instead.

---

## Brain tools (ordered by typical call sequence)

| Tool                              | Purpose | Required args | Notes |
|-----------------------------------|---------|---------------|-------|
| `local_sd_flux_check_deps`        | Quick dep / preset overview without contacting ComfyUI | — | Use first to confirm presets are loaded. |
| `local_sd_flux_rank_providers`    | Rank candidate ComfyUI hosts by 7-dim score | `candidates: [...]` | Use when the user has > 1 host (e.g. local + a friend's machine). |
| `local_sd_flux_create`            | Submit an image-generation job | `prompt: str` | Optional `preset_id`, `overrides`. |
| `local_sd_flux_status`            | Get the status of one job | `task_id: str` | |
| `local_sd_flux_list`              | List recent jobs | — | |
| `local_sd_flux_cancel`            | Interrupt the running prompt | `task_id: str` | ComfyUI only cancels the *running* prompt, not queued ones — see "common pitfalls". |

## HTTP routes

```
GET    /healthz                           // dep summary
GET    /check-deps                        // dep summary (no ComfyUI hit)
GET    /check-server                      // probes /system_stats; reports {ok, devices}
GET    /presets                           // list of normalised preset specs
GET    /config                            // user defaults
POST   /config                            // patch defaults (string values)
POST   /preview                           // build & return a plan WITHOUT running
POST   /rank-providers                    // rank candidates (uses contrib provider_score)
POST   /tasks                             // create + queue
GET    /tasks                             // list
GET    /tasks/{task_id}                   // get one
POST   /tasks/{task_id}/cancel            // best-effort cancel via /interrupt
DELETE /tasks/{task_id}                   // hard delete
GET    /tasks/{task_id}/image/{idx}       // stream image bytes
GET    /uploads/{rel_path}                // SDK upload preview helper
```

---

## The pipeline (one image)

1. **plan_image** — validate inputs (`prompt`, `output_dir`,
   `preset_id` ∈ {`sd15_basic`, `sdxl_basic`, `flux_basic`}, sizes,
   `timeout_sec` ∈ [10, 3600]).  When `custom_workflow` is given we
   skip the preset, mark `is_custom_workflow=True`, and pass the graph
   through verbatim so the user can ship any node graph they want.
2. **submit_prompt** — `POST /prompt`; ComfyUI replies with
   `prompt_id`.  We treat a non-empty `node_errors` map as a fatal
   client error (no retry) so the user immediately sees "your
   checkpoint is missing".
3. **poll loop** — `GET /history/{prompt_id}` every
   `poll_interval_sec` (default 1s, clamped to [0.1, 30]) until
   `outputs` appears or the wall-clock budget expires.
4. **download** — for every image referenced in `outputs.{node}.images`,
   `GET /view?filename=...&subfolder=...&type=...` and write
   `{output_dir}/{prompt_id}_{stem}.{ext}` to disk.
5. **verify (D2.10)** — produce a `Verification` envelope that flags:
   * 0 images returned, or
   * 0-byte downloads, or
   * custom workflow lacking a `SaveImage`-style node, or
   * elapsed > 80 % of `timeout_sec` (queue saturation).

---

## Quality gates we enforce

| Gate | Rule |
|------|------|
| G1 prompt non-empty | `QualityGates.check_input_integrity(non_empty_strings=["prompt"])`; rejected as 400. |
| G2 preset known | Unknown `preset_id` → `ValueError`; surfaced as 400 + ErrorCoach hint. |
| G3 budget bounded | `timeout_sec ∉ [10, 3600]` → `ValueError`. |
| G4 custom workflow non-empty | Empty dict → `ValueError`. |
| G5 verification | `to_verification` runs after every successful job and writes the envelope to `result.verification`. |

## Common failure modes (ErrorCoach hints will surface these)

| Symptom | Likely cause | What to suggest |
|---------|--------------|-----------------|
| `Network error: ConnectError` on `/system_stats` | ComfyUI not running | Start ComfyUI with `python main.py --listen` and confirm `default_base_url`. |
| `node_errors: {"X": "Value not in list ..."}` | Checkpoint or sampler unknown to this ComfyUI install | Install the model in `ComfyUI/models/checkpoints/` or pick a different `checkpoint`/`sampler`. |
| 0 images, `verified=False` | Preset's `SaveImage` node was edited away in `custom_workflow` | Add a `SaveImage` (or `SaveImageWebsocket`) sink. |
| All images are 0 bytes | Disk is full or ComfyUI worker crashed mid-write | Free disk space; check ComfyUI server logs for stack traces. |
| Cancel returned 200 but render kept going | The cancelled task was *queued* not *running*; ComfyUI's `/interrupt` only stops the running one | Either wait for the queue to drain or restart ComfyUI. |

---

## Reuse patterns

* **`comfy_client.ComfyClient`** is `BaseVendorClient`-derived → all
  retries, moderation handling, timeouts and `cancel_task` discipline
  are inherited.  Subclass it in another plugin only if you need a
  different *transport* (e.g. ComfyUI over websockets).
* **`workflow_presets`** is a registry — adding a new preset is one
  function returning `(workflow, PresetSpec)`.  No engine changes
  required.
* **`provider_score`** integration lets the plugin be the canonical
  "GPU placement" advisor for storyboards / video pipelines that
  need per-shot images.

## New-contributor notes

* ComfyUI's `/prompt` body uses **string node IDs** even when the IDs
  look numeric — keep them as `str` everywhere.
* FLUX **requires `cfg=1.0`**; we ship a preset that locks it but
  override-validation does NOT enforce that — adding a guard would
  break legitimate experimentation.  Surface a yellow flag in
  `to_verification` if you ever decide to.
* Image filenames coming back from ComfyUI may include subfolders
  (`subfolder="sub"`).  We always rewrite the basename to
  `{prompt_id}_{stem}.{ext}` on disk so two jobs that both produced
  `ComfyUI_00001_.png` don't collide.
