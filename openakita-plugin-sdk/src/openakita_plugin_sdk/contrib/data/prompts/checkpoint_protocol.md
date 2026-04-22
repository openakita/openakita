# Asset: P3.5 — Checkpoint Protocol (manifest-driven + sample approval)
# Source: OpenMontage skills/meta/checkpoint-protocol.md (lines 1-150, condensed)
# Use: IntentVerifier checkpoint trigger manifest — pair with the
#      `intent_verifier.py` SDK module which decides *when* to invoke
#      this protocol based on stage manifest fields.

# Checkpoint Protocol — Meta Skill

## When to Use

After completing a stage's work AND passing review.  This protocol teaches
*when* and *how* to checkpoint, and when to ask the human for approval.
Checkpoints are the save points of a pipeline.  They enable resume-from-failure,
human oversight, and audit trails.

## Protocol

### Step 1: Check Manifest Policy

Read the current stage's configuration from the pipeline manifest:

```yaml
- name: idea
  checkpoint_required: true
  human_approval_default: true
```

| `checkpoint_required` | `human_approval_default` | Action |
|----------------------|--------------------------|--------|
| true | true  | Checkpoint + present to human for approval |
| true | false | Checkpoint + proceed automatically |
| false | * | Skip checkpoint entirely (rare) |

### Step 2: Prepare Checkpoint Data

Gather everything needed:

1. **Stage name** — which stage just completed
2. **Status** — `"completed"` (or `"awaiting_human"` if approval needed)
3. **Artifacts** — the canonical artifact(s) produced by this stage
4. **Metadata** — review findings, cost snapshot, timing info

### Step 3: Write Checkpoint

Use the SDK helper:

```python
from openakita_plugin_sdk.contrib import take_checkpoint
take_checkpoint(stage_name=..., status=..., artifacts=..., metadata=...)
```

The helper will validate the artifact against its schema, write the JSON to
disk, and include timestamp + stage metadata.

### Step 4: Human Approval (If Required)

When `human_approval_default: true`:

1. **Present a summary** to the human (artifact key details, review findings,
   cost so far, action required).
2. **Wait for human response:**
   - **Approved** → update checkpoint status to `"completed"`, proceed to next stage.
   - **Revision requested** → go back to the stage with the human's feedback.
   - **Abort** → stop the pipeline.
3. **Approval-typical stages:**
   - `idea` — Always.  Creative direction defines everything downstream.
   - `script` — Always.  Words are the foundation.
   - `scene_plan` — Usually.  Visual choices are subjective.
   - `assets` — Rarely.  Automated quality checks suffice.
   - `edit` — Rarely.  Technical assembly, not creative.
   - `compose` — Rarely.  But human may want to preview.
   - `publish` — Always.  Human must approve before anything goes public.

### Step 5: Determine Next Stage

Read all existing checkpoints; the next stage is the first one whose
`checkpoint_required` is true and which has no recorded checkpoint yet.

### Step 6: Resume Protocol

At the START of any pipeline run, always check for existing progress.  If a
checkpoint exists with status `"awaiting_human"`, present its data and wait
for approval before proceeding.

## Sample Checkpoint (Reference-Driven Productions)

When a production is reference-driven (a `VideoAnalysisBrief` exists), there is
an additional checkpoint between proposal approval and full production:

| Stage | checkpoint_required | human_approval_default | Notes |
|-------|---------------------|------------------------|-------|
| `sample` | true | true | Always requires human approval |

The sample checkpoint:
1. Presents a rendered sample clip (10-15 seconds).
2. Cost snapshot: sample cost vs. projected full-video cost.
3. Action: approve (→ proceed to script), revise (→ re-generate), abort.

## Key Principles

1. **Always checkpoint completed work.**  Even when not required, checkpoint if
   the stage took significant time or cost.  Losing work is worse than an extra
   file on disk.
2. **Never skip human approval on creative stages.**  `idea` and `script` shape
   everything.  Rushing past them to save time produces output nobody wants.
3. **Include cost snapshots.**  The human must know how much has been spent and
   how much remains before approving expensive downstream stages.
4. **Checkpoints enable resume.**  If the pipeline crashes at `compose`, the
   human can restart and it picks up from `compose` — not from `idea`.
5. **Be transparent in approval requests.**  Don't just show the artifact —
   show the review findings, the cost, and any concerns.
