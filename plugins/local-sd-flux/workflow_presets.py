"""ComfyUI workflow presets — SD 1.5 / SDXL / FLUX.

Each preset is a function returning a *new* workflow dict so callers can
mutate freely without polluting future jobs.  We deliberately keep the
presets minimal — the user always has the escape hatch of
``custom_workflow=`` to inject any node graph they want.

The shape mirrors ``ComfyUI/server.py`` ``POST /prompt`` body: a
mapping ``{node_id: {"class_type": str, "inputs": dict}}``.  Node IDs
are arbitrary strings; we use small integers here so logs stay readable.

Why this lives outside ``image_engine.py``:
* the engine never touches "what nodes mean" — it only knows
  "submit + poll + download",
* presets are the bit users will customise most often (different
  checkpoints, samplers, schedulers), so isolating them keeps the
  diff surface tiny when somebody swaps a checkpoint name.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

__all__ = [
    "PRESET_BUILDERS",
    "PRESET_IDS",
    "PresetSpec",
    "apply_overrides",
    "build_preset_workflow",
    "describe_preset",
    "list_presets",
    "preset_default_overrides",
]


# ── Public dataclass-ish: PresetSpec ──────────────────────────────────


class PresetSpec(dict[str, Any]):
    """Thin wrapper around dict so :func:`describe_preset` reads cleanly."""

    @property
    def family(self) -> str:
        return str(self.get("family", "sd"))

    @property
    def checkpoint_default(self) -> str:
        return str(self.get("checkpoint_default", ""))

    @property
    def width_default(self) -> int:
        return int(self.get("width_default", 512))

    @property
    def height_default(self) -> int:
        return int(self.get("height_default", 512))

    @property
    def steps_default(self) -> int:
        return int(self.get("steps_default", 20))


# ── SD 1.5 ────────────────────────────────────────────────────────────


def _sd15_basic() -> tuple[dict, PresetSpec]:
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "{prompt}", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "{negative}", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {
                  "model": ["1", 0],
                  "positive": ["2", 0],
                  "negative": ["3", 0],
                  "latent_image": ["4", 0],
                  "seed": 0,
                  "steps": 20,
                  "cfg": 7.0,
                  "sampler_name": "euler",
                  "scheduler": "normal",
                  "denoise": 1.0,
              }},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "openakita_sd15"}},
    }
    spec = PresetSpec(
        family="sd",
        checkpoint_default="v1-5-pruned-emaonly.safetensors",
        width_default=512, height_default=512, steps_default=20,
        cfg_default=7.0, sampler_default="euler", scheduler_default="normal",
        notes="Stable Diffusion 1.5 baseline (512×512, 20 steps, CFG 7).",
    )
    return wf, spec


# ── SDXL ──────────────────────────────────────────────────────────────


def _sdxl_basic() -> tuple[dict, PresetSpec]:
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "{prompt}", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "{negative}", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {
                  "model": ["1", 0],
                  "positive": ["2", 0],
                  "negative": ["3", 0],
                  "latent_image": ["4", 0],
                  "seed": 0,
                  "steps": 28,
                  "cfg": 6.0,
                  "sampler_name": "dpmpp_2m",
                  "scheduler": "karras",
                  "denoise": 1.0,
              }},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "openakita_sdxl"}},
    }
    spec = PresetSpec(
        family="sdxl",
        checkpoint_default="sd_xl_base_1.0.safetensors",
        width_default=1024, height_default=1024, steps_default=28,
        cfg_default=6.0, sampler_default="dpmpp_2m", scheduler_default="karras",
        notes="SDXL base (1024×1024, 28 steps, dpmpp_2m + karras, CFG 6).",
    )
    return wf, spec


# ── FLUX ──────────────────────────────────────────────────────────────


def _flux_basic() -> tuple[dict, PresetSpec]:
    """FLUX.1 [dev] basic workflow.

    FLUX uses a different node topology from SD/SDXL — the UNet, two
    CLIP models and the VAE are loaded *separately* (rather than from
    a single .ckpt), and CFG is fixed at 1.0 (FLUX doesn't use CFG the
    same way).  The graph below mirrors the official workflow shipped
    in the ComfyUI examples.
    """
    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "flux1-dev.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader",
              "inputs": {
                  "clip_name1": "t5xxl_fp16.safetensors",
                  "clip_name2": "clip_l.safetensors",
                  "type": "flux",
              }},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "{prompt}", "clip": ["2", 0]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "6": {"class_type": "KSampler",
              "inputs": {
                  "model": ["1", 0],
                  "positive": ["4", 0],
                  "negative": ["4", 0],     # FLUX ignores negative; reuse positive node to keep graph valid
                  "latent_image": ["5", 0],
                  "seed": 0,
                  "steps": 20,
                  "cfg": 1.0,               # FLUX requires CFG=1
                  "sampler_name": "euler",
                  "scheduler": "simple",
                  "denoise": 1.0,
              }},
        "7": {"class_type": "VAEDecode",
              "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage",
              "inputs": {"images": ["7", 0], "filename_prefix": "openakita_flux"}},
    }
    spec = PresetSpec(
        family="flux",
        checkpoint_default="flux1-dev.safetensors",
        width_default=1024, height_default=1024, steps_default=20,
        cfg_default=1.0, sampler_default="euler", scheduler_default="simple",
        notes="FLUX.1 [dev] (1024×1024, 20 steps, CFG must stay at 1.0).",
    )
    return wf, spec


# ── Registry ──────────────────────────────────────────────────────────


PRESET_BUILDERS: dict[str, Callable[[], tuple[dict, PresetSpec]]] = {
    "sd15_basic": _sd15_basic,
    "sdxl_basic": _sdxl_basic,
    "flux_basic": _flux_basic,
}

PRESET_IDS: tuple[str, ...] = tuple(PRESET_BUILDERS.keys())


def list_presets() -> list[str]:
    """Return the IDs of every available preset (stable order)."""
    return list(PRESET_IDS)


def describe_preset(preset_id: str) -> PresetSpec:
    """Return the :class:`PresetSpec` for ``preset_id``.

    Raises :class:`KeyError` for unknown IDs so callers can pattern-match
    on the exception type.
    """
    if preset_id not in PRESET_BUILDERS:
        raise KeyError(f"unknown preset {preset_id!r}; known: {list(PRESET_IDS)}")
    _, spec = PRESET_BUILDERS[preset_id]()
    return spec


def build_preset_workflow(preset_id: str) -> dict:
    """Return a fresh deep copy of the workflow graph for ``preset_id``."""
    if preset_id not in PRESET_BUILDERS:
        raise KeyError(f"unknown preset {preset_id!r}; known: {list(PRESET_IDS)}")
    wf, _ = PRESET_BUILDERS[preset_id]()
    return deepcopy(wf)


# ── Override application ──────────────────────────────────────────────


def preset_default_overrides(preset_id: str) -> dict[str, Any]:
    """Return the default override dict for ``preset_id``.

    The keys are the user-facing parameters (``prompt``, ``negative``,
    ``seed``, ``steps``, ``cfg``, ``sampler``, ``scheduler``, ``width``,
    ``height``, ``checkpoint``, ``filename_prefix``); values are pulled
    from the spec.  Callers merge user-supplied values on top.
    """
    spec = describe_preset(preset_id)
    return {
        "prompt": "",
        "negative": "",
        "seed": 0,
        "steps": spec.steps_default,
        "cfg": float(spec.get("cfg_default", 7.0)),
        "sampler": str(spec.get("sampler_default", "euler")),
        "scheduler": str(spec.get("scheduler_default", "normal")),
        "width": spec.width_default,
        "height": spec.height_default,
        "checkpoint": spec.checkpoint_default,
        "filename_prefix": f"openakita_{spec.family}",
    }


def apply_overrides(workflow: dict, overrides: dict[str, Any]) -> dict:
    """Mutate ``workflow`` in place applying user overrides.

    Returns the workflow for chaining.  We walk every node and patch
    inputs whose value matches one of our placeholder strings or whose
    key matches a well-known parameter.  Nodes the workflow doesn't have
    are silently skipped — that lets a future preset add extra inputs
    without breaking back-compat.

    Override keys recognised:

    * ``prompt``  → ``CLIPTextEncode`` whose ``text`` contains ``"{prompt}"``
    * ``negative`` → ``CLIPTextEncode`` whose ``text`` contains ``"{negative}"``
    * ``seed`` / ``steps`` / ``cfg`` / ``sampler`` / ``scheduler`` →
      ``KSampler`` inputs (sampler/scheduler use ``sampler_name`` /
      ``scheduler`` keys inside the node)
    * ``width`` / ``height`` → ``EmptyLatentImage``
    * ``checkpoint`` → ``CheckpointLoaderSimple`` (SD/SDXL) or
      ``UNETLoader`` (FLUX)
    * ``filename_prefix`` → ``SaveImage``
    """
    for node in workflow.values():
        ct = node.get("class_type")
        ins = node.setdefault("inputs", {})

        if ct == "CLIPTextEncode":
            text = ins.get("text", "")
            if isinstance(text, str):
                if "{prompt}" in text and "prompt" in overrides:
                    ins["text"] = text.replace("{prompt}", str(overrides["prompt"]))
                if "{negative}" in text and "negative" in overrides:
                    ins["text"] = ins["text"].replace(
                        "{negative}", str(overrides["negative"]),
                    )

        elif ct == "EmptyLatentImage":
            if "width" in overrides:
                ins["width"] = int(overrides["width"])
            if "height" in overrides:
                ins["height"] = int(overrides["height"])

        elif ct == "KSampler":
            if "seed" in overrides:
                ins["seed"] = int(overrides["seed"])
            if "steps" in overrides:
                ins["steps"] = int(overrides["steps"])
            if "cfg" in overrides:
                ins["cfg"] = float(overrides["cfg"])
            if "sampler" in overrides:
                ins["sampler_name"] = str(overrides["sampler"])
            if "scheduler" in overrides:
                ins["scheduler"] = str(overrides["scheduler"])

        elif ct == "CheckpointLoaderSimple":
            if "checkpoint" in overrides:
                ins["ckpt_name"] = str(overrides["checkpoint"])

        elif ct == "UNETLoader":
            if "checkpoint" in overrides:
                ins["unet_name"] = str(overrides["checkpoint"])

        elif ct == "SaveImage":
            if "filename_prefix" in overrides:
                ins["filename_prefix"] = str(overrides["filename_prefix"])

    return workflow
