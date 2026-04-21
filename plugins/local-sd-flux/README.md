# local-sd-flux

> Generate images locally via your own ComfyUI server — Stable
> Diffusion 1.5 / SDXL / FLUX presets, plus a 7-dim provider ranker
> for picking between local-GPU / local-CPU / a remote ComfyUI host.

## Why this plugin exists

Hosted image vendors (DashScope, Replicate, fal.ai) work great but
they cost money per image and won't render NSFW or experimental
checkpoints.  ComfyUI, on the other hand, is the de-facto local
backend the OSS Stable Diffusion / FLUX community standardises on —
but driving it from a plugin requires building a workflow JSON, POSTing
it to `/prompt`, polling `/history`, and downloading from `/view`.
This plugin does all of that, plus:

* Ships **three battle-tested workflow presets** so the user only has
  to type a prompt.
* Exposes **`custom_workflow`** as an escape hatch — paste any node
  graph from the ComfyUI UI's "Save (API Format)" button and we'll
  run it.
* Wraps the SDK's **`provider_score`** so callers (the storyboard
  plugin, batch jobs) can ask "which ComfyUI host should this shot
  go to?" instead of hard-coding one URL.
* Returns a **D2.10 verification envelope** flagging zero-output,
  zero-byte and queue-saturation cases so the agent can decide
  whether to retry.

## Setup (one-time)

### 1. Install + run ComfyUI locally

```bash
git clone https://github.com/comfyanonymous/ComfyUI
cd ComfyUI
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py --listen 127.0.0.1 --port 8188
```

### 2. Download at least one model

Drop checkpoints into `ComfyUI/models/checkpoints/`:

| Preset       | Default checkpoint               | Where to get it |
|--------------|----------------------------------|-----------------|
| `sd15_basic` | `v1-5-pruned-emaonly.safetensors`| https://huggingface.co/runwayml/stable-diffusion-v1-5 |
| `sdxl_basic` | `sd_xl_base_1.0.safetensors`     | https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0 |
| `flux_basic` | `flux1-dev.safetensors` + `t5xxl_fp16.safetensors` + `clip_l.safetensors` + `ae.safetensors` | https://huggingface.co/black-forest-labs/FLUX.1-dev |

### 3. (Optional) Point this plugin at a non-default URL

```bash
curl -X POST http://localhost:8000/plugins/local-sd-flux/config \
  -H 'Content-Type: application/json' \
  -d '{"default_base_url": "http://192.168.1.42:8188"}'
```

## Usage examples

### HTTP

```bash
# Sanity check
curl http://localhost:8000/plugins/local-sd-flux/check-server

# Pick a preset, then preview the resulting plan (no actual render)
curl -X POST http://localhost:8000/plugins/local-sd-flux/preview \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "a wizard cat on a stack of books", "preset_id": "sdxl_basic"}'

# Render
TID=$(curl -sX POST http://localhost:8000/plugins/local-sd-flux/tasks \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "a wizard cat on a stack of books", "preset_id": "sdxl_basic", "overrides": {"steps": 30, "seed": 12345}}' \
  | jq -r .task_id)

# Poll until done
curl http://localhost:8000/plugins/local-sd-flux/tasks/$TID

# Stream the first image
curl -o out.png http://localhost:8000/plugins/local-sd-flux/tasks/$TID/image/0
```

### Brain tool

The agent normally just calls:

```json
{
  "tool": "local_sd_flux_create",
  "args": {
    "prompt": "a wizard cat on a stack of books",
    "preset_id": "sdxl_basic",
    "overrides": {"steps": 30, "seed": 12345}
  }
}
```

### Provider ranking

Use this when the user has more than one ComfyUI box (laptop GPU +
desktop GPU + a friend's RTX 4090) and you need to pick the right
one:

```json
{
  "tool": "local_sd_flux_rank_providers",
  "args": {
    "candidates": [
      {"id": "laptop", "label": "Laptop GTX 1660", "base_url": "http://127.0.0.1:8188",
       "quality": 0.6, "speed": 0.4, "cost": 1.0, "reliability": 0.7,
       "control": 1.0, "latency": 1.0, "compatibility": 0.9},
      {"id": "desktop", "label": "Desktop RTX 3090", "base_url": "http://192.168.1.20:8188",
       "quality": 0.95, "speed": 0.9, "cost": 0.9, "reliability": 0.85,
       "control": 0.95, "latency": 0.85, "compatibility": 0.95}
    ]
  }
}
```

## Configuration

| Key                          | Default                          |
|------------------------------|----------------------------------|
| `default_preset_id`          | `sdxl_basic`                     |
| `default_base_url`           | `http://127.0.0.1:8188`          |
| `default_output_format`      | `png`                            |
| `default_poll_interval_sec`  | `1.0`                            |
| `default_timeout_sec`        | `300.0`                          |
| `default_auth_token`         | empty (set if you reverse-proxy ComfyUI behind auth) |

## Testing

```bash
pytest plugins/local-sd-flux/tests
```

(99 unit + integration tests — workflow presets, ComfyClient HTTP
behaviour, image_engine planning / running / verification, plugin
routes & brain tools.  No real ComfyUI server required.)
