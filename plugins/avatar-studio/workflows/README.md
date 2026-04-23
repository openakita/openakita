# avatar-studio Workflows

This directory stores workflow references for the RunningHub and local
ComfyUI backends.

## How to get a RunningHub workflow_id

1. Go to [RunningHub](https://www.runninghub.cn) and search for a
   digital-human workflow (e.g. "wan2.2-s2v", "animate-mix").
2. Open the workflow page — the URL looks like
   `https://www.runninghub.cn/#/workflow/1850925505116598274`.
3. The number at the end is the **workflow_id**. Copy it.
4. Paste it into avatar-studio Settings → RunningHub → Workflow Presets
   for the relevant mode.

**Tip**: Fork community workflows to your own account so they don't
disappear if the author deletes them.

## How to use a local ComfyUI workflow

1. Build or download a ComfyUI workflow `.json` file.
2. Save it anywhere on disk.
3. In avatar-studio Settings → Local ComfyUI → Workflow Presets, enter
   the full path or a workflow_id if your ComfyUI supports it.

## recommended.json

The `recommended.json` file contains curated workflow_id suggestions
displayed when users click the "Recommend" button in Settings. These
IDs are placeholders — fill them in once stable community workflows
are identified.
