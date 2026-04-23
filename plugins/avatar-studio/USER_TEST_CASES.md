# avatar-studio User Test Cases

Quick smoke-test checklist for verifying dual-backend functionality.

## Prerequisites

- DashScope API Key configured (阿里云百炼)
- OSS bucket configured (for DashScope backend)

---

## TC-1: Settings — Three Backend Sections

1. Open Settings tab.
2. Verify three peer-level sections: **阿里云 DashScope**, **RunningHub**, **本地 ComfyUI**.
3. In 阿里云 DashScope section, expand "OSS 对象存储" — verify it's a collapsible.
4. Fill RunningHub API Key → click 测试连接.
5. Set instance type to "Plus".
6. Expand "Workflow 预设" → enter a workflow_id for 照片说话.
7. In 本地 ComfyUI, enter `http://127.0.0.1:8188` → click 测试连接.

## TC-2: Settings — TTS Engine Toggle

1. In Settings, find the **TTS 引擎** section.
2. Select "Edge-TTS（免费）" → verify the Edge-TTS voice selector appears.
3. Switch back to "CosyVoice（百炼付费）" → verify voice selector hides.

## TC-3: Create — Backend Selection

1. Open Create tab.
2. Verify 5 mode chips: 照片说话 / 视频换嘴 / 视频换人 / 数字人合成 / 图生动作.
3. Below modes, verify **选择后端** with 3 chips: 阿里云 / RunningHub / 本地 ComfyUI.
4. Select "阿里云 DashScope" → ModelInfoCard shows DashScope pricing.
5. Select "RunningHub" → ModelInfoCard shows "按 RH 实际用量扣费".
6. Select "本地 ComfyUI" → ModelInfoCard shows "本地推理，无云端费用".

## TC-4: Create — Workflow Picker (non-DashScope)

1. Select RunningHub backend.
2. Verify "Workflow ID" selector appears below ModelInfoCard.
3. If a preset was configured in Settings, it should appear as the default option.
4. Select "自定义填写..." → verify a text input appears for manual entry.

## TC-5: Create — Pose Drive Mode

1. Select **图生动作** mode.
2. Verify it requires: 1 portrait image + 1 reference video.
3. Verify ModelInfoCard shows "wan2.2-animate-move" with std/pro pricing.
4. Upload test image + video → submit (DashScope backend).

## TC-6: Create — Submission Validation

1. Clear DashScope API Key in Settings.
2. Try submitting a task with DashScope backend → expect error toast "请先配置 DashScope API Key".
3. Select RunningHub backend without configuring key → expect error toast.
4. Configure RunningHub key but leave workflow_id empty → expect error toast about workflow_id.

## TC-7: VoicePicker — Dual Engine

1. In Settings, set TTS engine to Edge-TTS.
2. Open Create tab, select a mode that needs TTS (e.g., 照片说话).
3. Verify voice dropdown shows 12 Edge-TTS voices (云希, 晓晓, etc.).
4. Switch TTS engine back to CosyVoice in Settings.
5. Verify voice dropdown switches back to CosyVoice voices (龙小淳, etc.).

## TC-8: API — Catalog Returns Edge Voices

```
GET /api/plugins/avatar-studio/catalog
```

Verify response includes `edge_voices` array with 12 entries and
`model_registry` array with entries for all 5 modes × 3 backends.
