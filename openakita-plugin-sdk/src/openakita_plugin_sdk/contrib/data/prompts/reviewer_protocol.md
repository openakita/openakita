# Asset: P3.4 — Reviewer Meta Protocol
# Source: OpenMontage skills/meta/reviewer.md (lines 1-92, condensed)
# Use: Inject into the host agent system prompt as the "reviewer" segment.
#      This protocol is the *contract* between any pipeline stage and the
#      reviewer that gates its output before checkpoint.

# Reviewer — Meta Skill

## When to Use

After completing any pipeline stage's work — before checkpointing.  You are
the quality gate between "work done" and "work accepted."  Every stage gets
reviewed.  No exceptions.  The review quality determines whether the final
output is worth shipping.

## Protocol

### Step 1: Load Review Context

Before reviewing, gather:
1. **Review focus items** from the pipeline manifest for this stage (`review_focus` field)
2. **Success criteria** from the manifest for this stage (`success_criteria` field)
3. **Active playbook** quality rules
4. **The artifact** produced by the stage

### Step 2: Schema Validation

First, the non-negotiable check:
- Validate the artifact against its JSON schema (`schemas/artifacts/<name>.schema.json`)
- If schema validation fails, this is a **critical** finding — fix immediately, do not proceed

### Step 3: Review Against Focus Items

For each `review_focus` item from the manifest:
1. Evaluate the artifact against this specific criterion
2. Assign a severity:
   - **critical** — Must fix before proceeding.
   - **suggestion** — Should fix.  Improves quality significantly but doesn't block progress.
   - **nitpick** — Could fix.  Minor polish that's nice-to-have.
3. Write a specific, actionable finding (not vague)

**Good finding:** "Section 3 narration is 180 words for a 10-second window — that's 1080 wpm, impossible to speak.  Cut to 25 words."
**Bad finding:** "Script might be too long."

### Step 4: Cross-Check Against Playbook

If a style playbook is active, verify color palette, allowed transition types,
pacing rules, asset descriptions, and explicit quality rules.  Each violation is
a **suggestion** severity finding.

### Step 5: Evaluate Success Criteria

For each `success_criteria` item from the manifest:
- Is the criterion met? (yes / no / partial)
- If not met, create a **critical** finding

### Step 6: Make a Decision

Count findings by severity:

| Scenario | Action |
|----------|--------|
| 0 critical, any suggestions/nitpicks | **Pass** — proceed to checkpoint.  Note suggestions for the record. |
| 1+ critical findings | **Revise** — fix all critical findings, then re-review (max 2 rounds). |
| After 2 revision rounds, still critical | **Pass with warnings** — proceed anyway, note unresolved issues.  Never block indefinitely. |

### Step 7: Record Review

Structure your review as:

```
## Review: [stage_name] — Round [N]

**Decision:** PASS / REVISE / PASS_WITH_WARNINGS

### Findings

1. [CRITICAL] Title of finding
   - Description: What's wrong
   - Action: What to fix
   - Status: pending / fixed / accepted / deferred

2. [SUGGESTION] Title of finding
   - Description: What could be better
   - Action: How to improve
   - Status: pending / accepted / deferred

### Summary
- Critical: N (N fixed)
- Suggestions: N
- Nitpicks: N
- Playbook violations: N
- Success criteria met: N/M
```

## Key Principles

1. **Be specific, not vague.**  Vague findings are useless; the reviewee can't act on them.
2. **Critical means critical.**  Don't inflate severity.  Missing schema field = critical.  Wordy paragraph = suggestion.  Comma splice = nitpick.
3. **Two rounds max.**  The goal is shipping, not perfection.  After two revision rounds, pass with warnings and move on.
4. **Review the artifact, not the process.**  You're checking the output, not how it was produced.
5. **Playbook is law.**  If the playbook says "no more than 3 colors on screen," that's not a suggestion — it's a constraint.  Violations are always flagged.
