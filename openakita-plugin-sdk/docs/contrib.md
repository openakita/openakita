# `openakita_plugin_sdk.contrib` — AI Media Plugin Scaffolding

A focused subpackage for AI **video / image / audio** plugins.  Pulled out
of the homologous code in `seedance-video` / `tongyi-image` so every new
plugin starts with the same foundation instead of re-inventing it.

> **Stability**: experimental but used by all P1+P2 first-party plugins.
> Pinned alongside the SDK; semver compatibility from 0.4.0 onwards.

## Why a separate subpackage

- The base SDK (`PluginBase`, `PluginAPI`, decorators, …) is intentionally
  **minimal** so it loads fast and has zero hard deps beyond stdlib +
  `pydantic`.
- AI media work needs more (HTTP retries, FFmpeg builders, error coach
  patterns, cost previews, …).  `contrib` carries it without polluting
  the core API.
- All `contrib` modules import their heavy deps (`aiosqlite`, `httpx`)
  **lazily** inside methods so plugins that do not touch DB / HTTP keep
  loading instantly.

## Module map

Status legend: ✅ used by ≥1 first-party plugin · 🅿️ available, awaiting
first consumer (still tested + maintained, free to import).

| module                  | status | what                                                                 |
|-------------------------|--------|----------------------------------------------------------------------|
| `task_manager`          | ✅      | `BaseTaskManager` — SQLite tasks/assets/config + cancel              |
| `vendor_client`         | ✅      | `BaseVendorClient` — async HTTP w/ retry classification + cancel hook|
| `errors`                | ✅      | `ErrorCoach` + `ErrorPattern` — render exceptions as 3-段式          |
| `cost_estimator`        | ✅      | `CostEstimator` + `CostPreview` — {low, high, sample, confidence}    |
| `intent_verifier`       | ✅      | `IntentVerifier` — "verify, not guess" pre-flight (AnyGen)           |
| `prompt_optimizer`      | ✅      | `PromptOptimizer` — vendor-agnostic LLM refinement                   |
| `quality_gates`         | ✅      | `QualityGates` — G1/G2/G3 pure functions (CI + SKILL.md)             |
| `render_pipeline`       | ✅      | `build_render_pipeline` — safe FFmpeg command builder                |
| `env_any_loader`        | ✅      | `load_env_any` — parse SKILL.md frontmatter env_any                  |
| `slideshow_risk`        | ✅      | `evaluate_slideshow_risk` — 6-dim heuristic (OpenMontage corrected)  |
| `delivery_promise`      | ✅      | `validate_cuts` — actual vs promised motion ratio                    |
| `provider_score`        | ✅      | `score_providers` — 7-dim weighted ranking (audit3 weights)          |
| `ui_events`             | ✅      | `UIEventEmitter` + `strip_plugin_event_prefix`                       |
| `ffmpeg`                | ✅      | `run_ffmpeg`, `auto_color_grade_filter`, `sample_signalstats`, …    |
| `verification`          | ✅      | `Verification` + `LowConfidenceField` (D2.10 envelope)               |
| `cost_tracker`          | 🅿️      | `CostTracker` — reserve/reconcile/refund + `requires_approval`       |
| `checkpoint`            | 🅿️      | `Checkpoint` — snapshot/restore around high-cost stages              |

### 🅿️ Modules — health-check addendum (Sprint 13.4)

`cost_tracker` and `checkpoint` are fully designed, fully tested
(44 unit tests as of 2026-04-20) and exported from
`openakita_plugin_sdk.contrib`, but have **no first-party plugin
consumer yet**.  They are kept in the SDK because:

1. **Future plugins need them.**  The roadmap items D7 (`dub-it`,
   long TTS jobs) and D5 (`ppt-to-video`, multi-stage LibreOffice +
   FFmpeg + TTS) are explicit consumers — both want
   ``reserve()`` / ``snapshot()`` semantics so a per-stage retry does
   not double-charge or leak the cost ledger.
2. **External plugin authors may already import them.**  The SDK
   commits to semver compat from 0.4.0; silently removing public
   symbols would break those users.
3. **Cheap to keep.**  Both modules are pure-Python, zero runtime cost
   when not imported, and the test suite runs in <1 s.

If a future audit shows neither D7 nor D5 ended up using them, raise
the question again — at that point a deprecation notice (one minor
release of warnings) is the right move, not a silent delete.

## Quick start (a typical plugin)

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.contrib import (
    BaseTaskManager, ErrorCoach, CostEstimator, IntentVerifier,
    UIEventEmitter, QualityGates,
)

class MyTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [("vendor_meta", "TEXT")]
    def default_config(self):
        return {"api_key": "", "poll_interval": "10"}

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._tm = MyTaskManager(api.get_data_dir() / "my.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._verifier = IntentVerifier(llm_call=api.get_brain().chat)

    async def on_create(self, body: dict) -> dict:
        gate = QualityGates.check_input_integrity(
            body, required=["prompt"], non_empty_strings=["prompt"],
        )
        if gate.blocking:
            rendered = self._coach.render(
                ValueError("input invalid"),
                raw_message=gate.message,
            )
            return {"ok": False, "error": rendered.to_dict()}

        intent = await self._verifier.verify(body["prompt"])
        if intent.clarifying_questions:
            return {"ok": False, "needs_confirmation": intent.to_dict()}

        # ... call vendor, persist, broadcast ...
        self._events.emit("task_created", {"id": "abc"})
        return {"ok": True, "task_id": "abc"}
```

## UI Kit (matching front-end widgets)

A set of zero-dep JS widgets ships under `openakita_plugin_sdk/web/ui-kit/`
and is auto-served at `/api/plugins/_sdk/ui-kit/<file>`:

| file                          | exports                                |
|-------------------------------|----------------------------------------|
| `styles.css`                  | shared theme-aware styles              |
| `event-helpers.js`            | `OpenAkita.onEvent` (auto-strip prefix)|
| `task-panel.js`               | `TaskPanel` class                      |
| `cost-preview.js`             | `CostPreview.{render,mount}`           |
| `error-coach.js`              | `ErrorCoach.{render,mount}` (UI side)  |
| `onboard-wizard.js`           | `OnboardWizard.askOnce`                |
| `first-success-celebrate.js`  | `FirstSuccessCelebrate.maybeFire`      |

Include in plugin HTML:

```html
<link rel="stylesheet" href="/api/plugins/_sdk/ui-kit/styles.css">
<script src="/api/plugins/_sdk/bootstrap.js"></script>
<script src="/api/plugins/_sdk/ui-kit/event-helpers.js"></script>
<script src="/api/plugins/_sdk/ui-kit/task-panel.js"></script>
<script src="/api/plugins/_sdk/ui-kit/cost-preview.js"></script>
<script src="/api/plugins/_sdk/ui-kit/error-coach.js"></script>
```

## Quality gates (G1 / G2 / G3)

Two-track per user decision (2026-04-18):

1. **Markdown protocol** — every plugin's `SKILL.md` documents the gates so
   the host agent can self-check.
2. **pytest CI** — the same `QualityGates` functions assert in CI.

Reference template for `SKILL.md`:

```md
## Quality gates

| Gate | What                       | Pass criteria                       |
|------|----------------------------|-------------------------------------|
| G1   | Input integrity            | required fields present & non-empty |
| G2   | Output schema              | result validates against MyResult   |
| G3   | Error readability          | ErrorCoach result, no fallback hit  |
```

## Backwards compatibility

Adding `contrib/` does not change any existing public symbol.  Old
plugins that import from `openakita_plugin_sdk` directly keep working.
