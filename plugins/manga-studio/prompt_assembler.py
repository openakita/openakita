"""manga-studio prompt assembler — pure functions for composing prompts.

This module is the *only* place that knows how to turn a structured
panel dict + character rows + visual style into a prompt string. The
reasons:

1. The pipeline calls into three different vendor APIs (DashScope
   wan2.7-image, Seedance 1.0 Lite I2V, Seedance 1.0 Lite T2V) — and
   workflow paths (RunningHub, ComfyUI). Keeping prompt assembly in one
   place is the only way the I2V → T2V fallback (Pixelle face-moderation
   recovery) preserves the same "voice" across models.
2. We unit-test the assembler with no fixtures, no monkeypatch, no
   network — pure dict in, prompt string out. ``_describe_character``
   alone has a dozen edge-cases (missing fields, empty appearance dict,
   unknown role) that are far cheaper to verify here than at the
   pipeline level.
3. Prompt length is the single biggest predictor of cost (and refusals)
   for both DashScope and Seedance. Token budgeting (``_clip_chars``)
   lives at this layer so the pipeline never accidentally builds a
   2 000-character prompt.

Pure-function discipline
-------------------------
- No asyncio, no I/O, no SDK imports.
- Everything is a frozen dataclass or a tuple-returning helper.
- Inputs are plain dicts (already-decoded character rows, already-parsed
  panel JSON). The pipeline does the JSON decode; we never re-decode.

Anti-pattern guardrails
-----------------------
- Pixelle C5: panels referencing an unknown character emit a logger
  warning but never raise — a hallucinated name from the LLM should
  degrade output quality, not crash the pipeline.
- Pixelle C2: every public function has a precise return contract; the
  callers can pattern-match on it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from manga_models import VisualStyleSpec

logger = logging.getLogger(__name__)


# ─── Public types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ImagePromptResult:
    """Output of ``compose_image_prompt``.

    Carries every parameter ``MangaWanxiangClient.submit_image`` needs:
    the positive prompt, the negative prompt, the (≤ 9) reference image
    URLs, and the aspect ratio. ``token_estimate`` is a coarse upper
    bound used by ``estimate_cost`` to compute the qwen line item.
    """

    prompt: str
    negative_prompt: str
    reference_image_urls: list[str] = field(default_factory=list)
    ratio: str = "9:16"
    token_estimate: int = 0


@dataclass(frozen=True)
class VideoPromptResult:
    """Output of ``compose_i2v_prompt`` / ``compose_t2v_prompt``."""

    prompt: str
    duration_sec: int
    has_reference_image: bool


# ─── Tunables ─────────────────────────────────────────────────────────────

# Hard caps below which we never get refusals from either DashScope or
# Seedance for prompt-too-long. The image prompt cap is generous (the
# wan2.7-image API accepts up to 800 chars); the video caps are tighter
# because Seedance applies an undocumented ~300 char ceiling.
MAX_IMAGE_PROMPT_CHARS = 800
MAX_VIDEO_PROMPT_CHARS = 300

# DashScope wan2.7-image accepts at most 9 reference images per call.
# Source: https://help.aliyun.com/zh/model-studio/wan-image-api
MAX_REF_IMAGES = 9


# ─── Public functions ─────────────────────────────────────────────────────


def compose_image_prompt(
    *,
    panel: dict[str, Any],
    characters: list[dict[str, Any]],
    style: VisualStyleSpec,
    ratio: str = "9:16",
    panel_index: int | None = None,
) -> ImagePromptResult:
    """Compose the text-to-image (or image-to-image with refs) prompt.

    Args:
        panel: One storyboard entry. Recognised keys
            ``narration`` / ``description`` / ``background`` / ``mood``
            / ``camera`` / ``action`` / ``characters_in_scene`` /
            ``dialogue`` are all optional.
        characters: Already-decoded rows from the ``characters`` table.
            Each must have at minimum ``id`` and ``name``; everything
            else is recovered defensively.
        style: A ``VisualStyleSpec`` from ``manga_models``. Drives the
            tail style fragment + the negative prompt seed.
        ratio: Aspect ratio passed through to the result.
        panel_index: Optional index used purely for log lines so the
            user can spot which panel triggered a missing-character
            warning.

    Returns:
        ``ImagePromptResult`` with positive prompt, negative prompt,
        reference URLs (already capped at 9), and the original ratio.
    """
    chars_by_name = {c.get("name", ""): c for c in characters if c.get("name")}
    chars_by_id = {c.get("id", ""): c for c in characters if c.get("id")}

    in_scene = _resolve_scene_characters(panel, chars_by_name, chars_by_id, panel_index)

    # ─── Positive prompt assembly ──────────────────────────────
    fragments: list[str] = []

    # 1. Visual style — always first so the diffusion model anchors on it.
    fragments.append(style.prompt_fragment.strip())

    # 2. Per-panel scene description (narration > description > "").
    scene_desc = _first_nonempty(panel, "description", "narration", "scene")
    if scene_desc:
        fragments.append(scene_desc.strip())

    # 3. Background — separate hint so the model treats it as setting,
    #    not subject.
    background = _first_nonempty(panel, "background", "setting")
    if background:
        fragments.append(f"setting: {background.strip()}")

    # 4. Character descriptions — every character in scene gets a
    #    1-sentence appearance line. We deduplicate by id so a panel
    #    that mentions the same character twice doesn't duplicate the
    #    prompt fragment.
    seen_ids: set[str] = set()
    for char in in_scene:
        cid = char.get("id", "")
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        char_desc = _describe_character(char)
        if char_desc:
            fragments.append(char_desc)

    # 5. Camera + composition — kept as English directives the diffusion
    #    model recognises.
    camera = _first_nonempty(panel, "camera", "shot", "composition")
    if camera:
        fragments.append(f"camera: {camera.strip()}")

    # 6. Mood / atmosphere.
    mood = _first_nonempty(panel, "mood", "atmosphere", "tone")
    if mood:
        fragments.append(f"mood: {mood.strip()}")

    # 7. Manga-specific finishers — clean panel + comic-page composition.
    fragments.append("clean comic panel composition, professional manga illustration")

    prompt_full = ", ".join(f for f in fragments if f)
    prompt_clipped = _clip_chars(prompt_full, MAX_IMAGE_PROMPT_CHARS)

    # ─── Negative prompt — style negative + manga-universal negatives ──
    neg_parts = [style.negative_prompt.strip()]
    neg_parts.append("text, watermark, logo, signature, low quality, deformed, extra limbs")
    if any(_first_nonempty(p, "no_text") for p in [panel]):
        # If the panel explicitly says "no text", we already cover it.
        pass
    negative_prompt = ", ".join(p for p in neg_parts if p)

    # ─── Reference images — pull from every in-scene character ─────────
    ref_urls: list[str] = []
    for char in in_scene:
        for url in _normalize_ref_images(char.get("ref_images_json")):
            if url not in ref_urls:
                ref_urls.append(url)
                if len(ref_urls) >= MAX_REF_IMAGES:
                    break
        if len(ref_urls) >= MAX_REF_IMAGES:
            break

    return ImagePromptResult(
        prompt=prompt_clipped,
        negative_prompt=negative_prompt,
        reference_image_urls=ref_urls,
        ratio=ratio,
        token_estimate=_token_estimate(prompt_clipped),
    )


def compose_i2v_prompt(
    *,
    panel: dict[str, Any],
    style: VisualStyleSpec,
    duration_sec: int = 5,
) -> VideoPromptResult:
    """Compose the prompt for Seedance image-to-video.

    The reference image already encodes the scene + characters, so the
    prompt's job is to tell Seedance HOW the image should move:

    - ``camera`` (push / pull / pan / orbit / handheld …)
    - ``action`` (what the subject does)
    - one ``mood`` keyword
    - a tail style anchor so the motion stays in the manga register

    Anything longer than ``MAX_VIDEO_PROMPT_CHARS`` is clipped — Seedance
    silently drops trailing chars over its undocumented ceiling.
    """
    parts: list[str] = []

    action = _first_nonempty(panel, "action", "movement", "motion")
    camera = _first_nonempty(panel, "camera", "shot", "composition")
    mood = _first_nonempty(panel, "mood", "atmosphere")
    duration = max(1, int(duration_sec))

    parts.append(f"{duration}s manga drama panel animation")
    if camera:
        parts.append(f"camera: {camera.strip()}")
    if action:
        parts.append(f"motion: {action.strip()}")
    if mood:
        parts.append(f"mood: {mood.strip()}")

    # Short style anchor — full ``prompt_fragment`` is too long for the
    # 300-char Seedance ceiling, so we lift just the head clause.
    style_anchor = style.prompt_fragment.split(",")[0].strip()
    if style_anchor:
        parts.append(style_anchor)

    parts.append("smooth camera movement, consistent character design")

    prompt = ", ".join(p for p in parts if p)
    prompt = _clip_chars(prompt, MAX_VIDEO_PROMPT_CHARS)
    return VideoPromptResult(prompt=prompt, duration_sec=duration, has_reference_image=True)


def compose_t2v_prompt(
    *,
    panel: dict[str, Any],
    characters: list[dict[str, Any]],
    style: VisualStyleSpec,
    duration_sec: int = 5,
) -> VideoPromptResult:
    """Compose the prompt for Seedance text-to-video (no reference image).

    Used for two cases:

    - The episode is rendered through the T2V path (no panel image step).
    - I2V got rejected by face-moderation and the pipeline auto-falls
      back to T2V (Pixelle anti-pattern: never silently drop a panel —
      always have a recovery path).

    Compared to the I2V version, T2V prompts must carry the *full* scene
    description because Seedance has no image to anchor on.
    """
    img = compose_image_prompt(panel=panel, characters=characters, style=style, ratio="9:16")
    motion_parts: list[str] = []
    camera = _first_nonempty(panel, "camera", "shot", "composition")
    action = _first_nonempty(panel, "action", "movement", "motion")
    duration = max(1, int(duration_sec))

    motion_parts.append(f"{duration}s manga drama animation")
    if camera:
        motion_parts.append(f"camera: {camera.strip()}")
    if action:
        motion_parts.append(f"motion: {action.strip()}")

    motion_lead = ", ".join(motion_parts)
    full = f"{motion_lead}. Scene: {img.prompt}"
    return VideoPromptResult(
        prompt=_clip_chars(full, MAX_VIDEO_PROMPT_CHARS),
        duration_sec=duration,
        has_reference_image=False,
    )


def compose_tts_text(
    panel: dict[str, Any],
    *,
    characters: list[dict[str, Any]] | None = None,
    fallback_voice: str = "zh-CN-XiaoxiaoNeural",
    include_narration: bool = True,
    include_dialogue: bool = True,
) -> tuple[str, str]:
    """Pick the spoken text + voice id for a panel.

    Priority (when both narration and dialogue exist):

    1. Dialogue from the FIRST speaking character — uses that
       character's ``default_voice_id`` if set.
    2. Narration — uses ``fallback_voice`` (a calm narrator voice).
    3. Empty result if neither — caller skips the TTS step for this
       panel and the FFmpeg mux fills it with a 0-duration silence.

    Returns ``(text, voice_id)``. An empty string for either signals
    "skip this panel's audio".
    """
    chars_by_name: dict[str, dict[str, Any]] = {}
    if characters:
        chars_by_name = {c.get("name", ""): c for c in characters if c.get("name")}

    if include_dialogue:
        dialogue = panel.get("dialogue") or []
        if isinstance(dialogue, list):
            for line in dialogue:
                if not isinstance(line, dict):
                    continue
                text = (line.get("line") or line.get("text") or "").strip()
                if not text:
                    continue
                speaker = (line.get("character") or line.get("speaker") or "").strip()
                voice = chars_by_name.get(speaker, {}).get("default_voice_id") or fallback_voice
                return text, voice

    if include_narration:
        narration = (panel.get("narration") or "").strip()
        if narration:
            return narration, fallback_voice

    return "", ""


# ─── Helpers ──────────────────────────────────────────────────────────────


def _describe_character(char: dict[str, Any]) -> str:
    """One-sentence appearance line, e.g.

    ``"hero Li Lei (male, 18-25, brave): short black hair, dark eyes, school uniform"``

    Defensive about every field — a half-filled character row should
    still produce *something* useful instead of crashing.
    """
    name = (char.get("name") or "").strip()
    if not name:
        return ""

    role = (char.get("role_type") or "").strip()
    gender = (char.get("gender") or "").strip()
    age = (char.get("age_range") or "").strip()
    personality = (char.get("personality") or "").strip()

    bracket: list[str] = []
    if gender and gender != "unknown":
        bracket.append(gender)
    if age:
        bracket.append(age)
    if personality:
        bracket.append(personality)
    bracket_str = f" ({', '.join(bracket)})" if bracket else ""

    head = f"{role} {name}{bracket_str}".strip()

    # appearance_json is the most variable field — could be a dict, a
    # str (legacy), or None. We accept all and skip silently if it's
    # unparseable.
    appearance = char.get("appearance_json") or {}
    if isinstance(appearance, str):
        # Legacy free-form text — use as-is.
        appearance_str = appearance.strip()
    elif isinstance(appearance, dict):
        appearance_str = ", ".join(
            f"{k.replace('_', ' ')}: {v}"
            for k, v in appearance.items()
            if v not in ("", None) and isinstance(v, (str, int, float))
        )
    else:
        appearance_str = ""

    desc = (char.get("description") or "").strip()

    tail_parts = [p for p in (appearance_str, desc) if p]
    tail = ", ".join(tail_parts)
    if not tail:
        return head
    return f"{head}: {tail}"


def _resolve_scene_characters(
    panel: dict[str, Any],
    chars_by_name: dict[str, dict[str, Any]],
    chars_by_id: dict[str, dict[str, Any]],
    panel_index: int | None,
) -> list[dict[str, Any]]:
    """Pull the characters referenced by ``panel`` out of either index.

    Accepts ``characters_in_scene`` as a list of names *or* ids — the
    LLM is inconsistent about which it returns and we don't want a
    regex-strict format check to crash the pipeline. Hits the by-id
    index first (faster, deterministic), then by-name.
    """
    refs = panel.get("characters_in_scene")
    if not isinstance(refs, list):
        # Backstop: harvest names from dialogue entries if no scene list.
        dialogue = panel.get("dialogue") or []
        refs = []
        if isinstance(dialogue, list):
            for line in dialogue:
                if isinstance(line, dict):
                    name = line.get("character") or line.get("speaker")
                    if name and name not in refs:
                        refs.append(name)

    out: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, str):
            continue
        char = chars_by_id.get(ref) or chars_by_name.get(ref)
        if char is None:
            logger.warning(
                "manga-studio panel %s: unknown character ref %r — skipping description",
                panel_index if panel_index is not None else "?",
                ref,
            )
            continue
        out.append(char)
    return out


def _normalize_ref_images(value: Any) -> list[str]:
    """Accept the multi-shape ``ref_images_json`` that the DB returns.

    The task manager auto-decodes JSON columns so the typical shape is
    ``list[str]``. We still accept ``list[dict]`` (some legacy rows
    stored ``[{"url": ...}, ...]``) and a raw JSON string (in case the
    caller bypassed the task manager).
    """
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                url = item.get("url") or item.get("image") or ""
                if isinstance(url, str) and url.strip():
                    out.append(url.strip())
        return out
    if isinstance(value, str):
        try:
            import json

            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return _normalize_ref_images(parsed)
    return []


def _first_nonempty(d: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string value for any of ``keys``."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _clip_chars(s: str, max_chars: int) -> str:
    """Trim ``s`` to at most ``max_chars`` UTF-16 code units (the unit
    DashScope and Seedance count). We slice on word boundaries when
    possible to avoid mid-word cuts that confuse the diffusion model."""
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    last_break = max(cut.rfind(", "), cut.rfind(". "))
    if last_break >= max_chars * 0.6:
        return cut[:last_break]
    return cut


def _token_estimate(s: str) -> int:
    """Rough token count: 1 token ≈ 4 chars for Latin / 1.5 chars for
    Han. We use 2.5 as a midpoint for mixed Chinese-English prompts."""
    if not s:
        return 0
    return max(1, int(len(s) / 2.5))
