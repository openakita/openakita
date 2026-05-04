"""manga-studio script writer — LLM-driven story → panel breakdown.

Calls the host's ``brain.access`` to expand a free-form story into a
strict storyboard JSON: ``{episode_title, summary, panels: [{idx,
narration, dialogue, characters_in_scene, camera, action, mood,
background}]}``.

Design parallels ``plugins/word-maker/word_brain_helper.py`` and
``plugins/ppt-maker/ppt_brain_adapter.py``:

- A small ``BrainResult`` envelope so the caller knows whether the
  brain was even reachable, plus the parsed dict and a fallback-when-
  brain-unavailable.
- Permission check via ``api.has_permission("brain.access")`` so the
  pipeline degrades gracefully when the user revokes the permission.
- ``parse_llm_json_object`` from ``manga_inline.llm_json_parser`` does
  the 5-level fallback (raw → fenced → balanced-brace → tail-only → fix
  trailing commas) so a flaky LLM output still parses to a usable dict.

Why a dedicated module instead of inlining into the pipeline:

1. The same writer is reused by ``manga_quick_drama`` (one-shot tool)
   and ``manga_split_script`` (re-roll script only). Both want the
   exact same prompt + parsing semantics.
2. Tests can hand a fake ``brain`` (a single coroutine method
   ``think``) and exercise every fallback path without booting the
   plugin host.

Anti-pattern guardrails
-----------------------
- Pixelle C3: the prompt is small + JSON-only. We never ask the LLM
  for narrative prose that we'll later regex out.
- Pixelle C5: the deterministic ``_fallback_panels`` is *not* a stub.
  When the brain is unavailable, the pipeline can still produce a
  valid storyboard from the user's story — we splice the story into
  ``n_panels`` slices and let DashScope generate something reasonable
  from the slice text alone.
- Pixelle C7: this module never reads ENV. The api_key for the brain
  is owned by the host; we just call the brain interface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from manga_inline.llm_json_parser import parse_llm_json_object

logger = logging.getLogger(__name__)


# ─── Result envelope ──────────────────────────────────────────────────────


@dataclass(slots=True)
class BrainResult:
    """Envelope mirroring the WordMaker / PPTMaker convention."""

    ok: bool
    data: dict[str, Any]
    error: str = ""
    used_brain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "used_brain": self.used_brain,
        }


class _BrainLike(Protocol):
    """Minimal duck-type contract.

    The host's brain may expose ``think_lightweight`` (cheap model),
    ``think`` (default model), or ``chat`` (raw chat-completion). We
    fall back through them in that order so the manga writer always
    works on the cheapest reachable channel.
    """

    async def think(self, *args: Any, **kwargs: Any) -> Any: ...


# ─── Public class ─────────────────────────────────────────────────────────


SYSTEM_PROMPT_ZH = """你是漫剧（漫画动画短剧）剧本拆分专家。

## 任务
把用户的故事概要拆成 N 个分镜（panels），每个分镜对应一张漫画图 + 一段图生视频。

## 输出
严格 JSON。不要包裹 Markdown 代码块。Schema 如下：

{
  "episode_title": "短剧标题（≤ 16 字）",
  "summary": "整集 1-2 句梗概",
  "panels": [
    {
      "idx": 0,
      "narration": "旁白叙述（≤ 50 字）",
      "dialogue": [{"character": "角色名", "line": "台词（≤ 30 字）"}],
      "characters_in_scene": ["角色名1", "角色名2"],
      "camera": "镜头语言（推/拉/摇/移/跟/环绕，可加镜距：远景/中景/特写）",
      "action": "本镜动作（10-25 字）",
      "mood": "氛围（紧张/温馨/悬疑/欢快/忧伤/史诗/治愈/恐怖/浪漫）",
      "background": "背景设定（10-30 字）"
    }
  ]
}

## 规则
- 必须返回严格 N 个分镜（不能多不能少）
- 每个分镜的台词 + 旁白合计 ≤ 80 字
- characters_in_scene 必须从「可用角色」里挑选（不能凭空捏造）
- 只输出 JSON，不要任何解释、不要 ```json``` 包裹
"""


def _build_user_prompt(
    *,
    story: str,
    n_panels: int,
    seconds_per_panel: int,
    available_characters: list[dict[str, Any]],
    visual_style_label: str,
) -> str:
    char_lines = []
    for c in available_characters:
        name = c.get("name", "")
        if not name:
            continue
        role = c.get("role_type") or "main"
        gender = c.get("gender") or "unknown"
        age = c.get("age_range") or ""
        personality = c.get("personality") or ""
        bits = [b for b in (gender, age, personality) if b and b != "unknown"]
        char_lines.append(f"- {name}（{role}，{', '.join(bits) if bits else '无补充信息'}）")

    char_block = "\n".join(char_lines) if char_lines else "（用户没有提供角色，可由你给主角起名）"

    return f"""## 故事概要
{story.strip()}

## 可用角色
{char_block}

## 风格
{visual_style_label}

## 节奏
拆成 {n_panels} 个分镜，每个分镜约 {seconds_per_panel} 秒。

请输出 JSON。
"""


class MangaScriptWriter:
    """LLM-backed storyboard writer.

    Args:
        api: The host ``PluginAPI`` (provides ``has_permission`` and
            ``get_brain``). Pass a stub in tests.
    """

    def __init__(self, api: Any) -> None:
        self._api = api

    # ── Capability probe ──────────────────────────────────────────

    def is_available(self) -> bool:
        has_permission = getattr(self._api, "has_permission", None)
        if callable(has_permission) and not has_permission("brain.access"):
            return False
        get_brain = getattr(self._api, "get_brain", None)
        return callable(get_brain) and get_brain() is not None

    def _get_brain(self) -> _BrainLike | None:
        has_permission = getattr(self._api, "has_permission", None)
        if callable(has_permission) and not has_permission("brain.access"):
            return None
        get_brain = getattr(self._api, "get_brain", None)
        if not callable(get_brain):
            return None
        return get_brain()

    # ── Brain dispatch ────────────────────────────────────────────

    async def _ask_llm(self, *, system_prompt: str, user_prompt: str) -> str:
        brain = self._get_brain()
        if brain is None:
            raise RuntimeError("brain.access not granted or no brain configured")

        # Walk the brain's surfaces in cost order.
        if hasattr(brain, "think_lightweight"):
            try:
                result = await brain.think_lightweight(  # type: ignore[attr-defined]
                    prompt=user_prompt, system=system_prompt, max_tokens=2000
                )
                text = _extract_text(result)
                if text.strip():
                    return text
            except Exception as exc:  # noqa: BLE001
                logger.warning("manga-studio: think_lightweight failed: %s", exc)

        if hasattr(brain, "think"):
            result = await brain.think(  # type: ignore[attr-defined]
                prompt=user_prompt, system=system_prompt, max_tokens=2000
            )
            text = _extract_text(result)
            if text.strip():
                return text
            raise RuntimeError("brain.think returned empty content")

        if hasattr(brain, "chat"):
            result = await brain.chat(  # type: ignore[attr-defined]
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )
            text = _extract_text(result)
            if text.strip():
                return text
            raise RuntimeError("brain.chat returned empty content")

        raise RuntimeError("brain has no think_lightweight / think / chat surface")

    # ── Public: write_storyboard ──────────────────────────────────

    async def write_storyboard(
        self,
        *,
        story: str,
        n_panels: int,
        seconds_per_panel: int = 5,
        characters: list[dict[str, Any]] | None = None,
        visual_style_label: str = "少年热血",
    ) -> BrainResult:
        """Expand ``story`` into a structured storyboard.

        Always returns a non-empty ``data`` dict — when the brain is
        unavailable or the LLM returns garbage, ``_fallback_panels``
        slices ``story`` into ``n_panels`` evenly-sized chunks so the
        pipeline can still produce a valid (if uninspired) episode.

        Args:
            story: Free-form story / synopsis from the user.
            n_panels: Exact number of panels we want back. The fallback
                respects this even when the LLM doesn't.
            seconds_per_panel: Hint for the LLM's pacing (and used to
                set per-panel duration in pipeline).
            characters: Pre-loaded character rows the LLM may reference
                in ``characters_in_scene``. Empty list ⇒ LLM is told it
                may name new characters.
            visual_style_label: Human-readable style label (e.g.
                ``"少年热血"``) — used purely as a tone hint, not as a
                hard constraint on the LLM output.

        Returns:
            ``BrainResult`` with ``data = {episode_title, summary, panels}``.
        """
        story_clean = (story or "").strip()
        if not story_clean:
            raise ValueError("story must not be empty")
        n_panels = max(1, int(n_panels))

        characters = characters or []
        fallback = _fallback_panels(
            story=story_clean,
            n_panels=n_panels,
            characters=characters,
        )

        if not self.is_available():
            return BrainResult(
                ok=True,
                data=fallback,
                error="brain.access not granted — using deterministic split",
                used_brain=False,
            )

        user_prompt = _build_user_prompt(
            story=story_clean,
            n_panels=n_panels,
            seconds_per_panel=seconds_per_panel,
            available_characters=characters,
            visual_style_label=visual_style_label,
        )

        try:
            raw = await self._ask_llm(
                system_prompt=SYSTEM_PROMPT_ZH,
                user_prompt=user_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("manga-studio: brain call failed: %s", exc)
            return BrainResult(ok=False, data=fallback, error=str(exc), used_brain=True)

        errors: list[str] = []
        parsed = parse_llm_json_object(raw, fallback=None, errors=errors)
        if not isinstance(parsed, dict) or not parsed:
            # Empty dict means parse_llm_json gave up after every level
            # of recovery — treat as a hard parse failure.
            joined = "; ".join(errors) or "LLM did not return parseable JSON"
            return BrainResult(ok=False, data=fallback, error=joined, used_brain=True)

        # Did the LLM at least emit a panels-shaped key? An empty
        # ``panels`` array (or none at all) means we got valid JSON
        # back but it's missing the only field we care about.
        raw_panels = parsed.get("panels") or parsed.get("storyboard") or []
        if not isinstance(raw_panels, list) or not raw_panels:
            return BrainResult(
                ok=False,
                data=fallback,
                error="LLM JSON missing 'panels' / 'storyboard' array",
                used_brain=True,
            )

        normalized = _normalize_storyboard(parsed, n_panels=n_panels)
        if not normalized["panels"]:
            return BrainResult(
                ok=False,
                data=fallback,
                error="LLM returned zero panels after normalization",
                used_brain=True,
            )
        return BrainResult(ok=True, data=normalized, used_brain=True)


# ─── Helpers ──────────────────────────────────────────────────────────────


def _extract_text(result: Any) -> str:
    """Normalise the various shapes brain methods return."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return str(result.get("content") or result.get("text") or "")
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    return str(result) if result is not None else ""


def _normalize_storyboard(parsed: dict[str, Any], *, n_panels: int) -> dict[str, Any]:
    """Coerce a possibly-loose LLM JSON into our canonical shape.

    - ``panels`` is forced to length ``n_panels`` (truncate if longer,
      synthesise empty panels if shorter — better to produce a valid
      script than to fail the whole episode).
    - Each panel gets ``idx`` re-numbered and ``dialogue`` /
      ``characters_in_scene`` defaulted to ``[]``.
    """
    title = str(parsed.get("episode_title") or parsed.get("title") or "未命名漫剧")
    summary = str(parsed.get("summary") or parsed.get("description") or "")

    raw_panels = parsed.get("panels") or parsed.get("storyboard") or []
    if not isinstance(raw_panels, list):
        raw_panels = []

    panels: list[dict[str, Any]] = []
    for idx in range(n_panels):
        src: dict[str, Any] = {}
        if idx < len(raw_panels) and isinstance(raw_panels[idx], dict):
            src = raw_panels[idx]
        panels.append(_normalize_panel(src, idx=idx))

    return {"episode_title": title, "summary": summary, "panels": panels}


def _normalize_panel(p: dict[str, Any], *, idx: int) -> dict[str, Any]:
    dialogue_raw = p.get("dialogue") or []
    dialogue: list[dict[str, str]] = []
    if isinstance(dialogue_raw, list):
        for line in dialogue_raw:
            if isinstance(line, dict):
                speaker = str(line.get("character") or line.get("speaker") or "")
                text = str(line.get("line") or line.get("text") or "")
                if text.strip():
                    dialogue.append({"character": speaker, "line": text})

    chars = p.get("characters_in_scene") or []
    if not isinstance(chars, list):
        chars = []
    chars = [str(c) for c in chars if isinstance(c, str) and c.strip()]

    return {
        "idx": idx,
        "narration": str(p.get("narration") or p.get("description") or ""),
        "dialogue": dialogue,
        "characters_in_scene": chars,
        "camera": str(p.get("camera") or p.get("shot") or ""),
        "action": str(p.get("action") or p.get("motion") or ""),
        "mood": str(p.get("mood") or p.get("atmosphere") or ""),
        "background": str(p.get("background") or p.get("setting") or ""),
    }


def _fallback_panels(
    *,
    story: str,
    n_panels: int,
    characters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic story → panels split when the brain is unavailable.

    Slices ``story`` into ``n_panels`` roughly equal chunks and emits
    panels with the slice as both narration and "description". The
    pipeline can still render reasonable images from these — the LLM
    is a quality booster, not a hard requirement.
    """
    text = story.strip().replace("\r\n", "\n")
    sentences = [
        s.strip() for s in text.replace("！", "。").replace("？", "。").split("。") if s.strip()
    ]

    if not sentences:
        sentences = [text or "故事开始"]

    # Group sentences into n_panels buckets.
    panels: list[dict[str, Any]] = []
    if n_panels <= 0:
        return {"episode_title": "未命名漫剧", "summary": text[:80], "panels": []}
    per_panel = max(1, len(sentences) // n_panels)
    char_names = [c.get("name", "") for c in characters if c.get("name")]
    main = char_names[0] if char_names else ""

    for idx in range(n_panels):
        start = idx * per_panel
        end = start + per_panel if idx < n_panels - 1 else len(sentences)
        chunk = "。".join(sentences[start:end]).strip()
        if not chunk:
            chunk = sentences[min(idx, len(sentences) - 1)]
        narration = chunk[:50]
        panels.append(
            {
                "idx": idx,
                "narration": narration,
                "dialogue": [],
                "characters_in_scene": [main] if main else [],
                "camera": "中景",
                "action": "镜头缓慢推近",
                "mood": "叙述",
                "background": "",
            }
        )
    return {
        "episode_title": "未命名漫剧",
        "summary": (text[:120] + "…") if len(text) > 120 else text,
        "panels": panels,
    }


__all__ = [
    "BrainResult",
    "MangaScriptWriter",
    "SYSTEM_PROMPT_ZH",
    "_build_user_prompt",
    "_fallback_panels",
    "_normalize_storyboard",
]
