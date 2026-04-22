"""bgm-mixer — beat-aware BGM ducking & mixing engine.

Why this engine exists (Sprint 12 / D2.8 from
``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md``):

    早期假设：BGM 只要按时长截一段、和人声叠起来就行
    真实情况：CutClaw 的 madmom 节拍剪辑参数表说明，剪辑点必须落在
                节拍上 (beat-snapped)；同时人声段需要做 ducking
                (BGM 自动压低 -8 ~ -12 dB)；起止位置必须淡入淡出
                (200 ~ 600 ms) 否则会有 click 噪声。
    影响：直接 ffmpeg amix 出来的成品像两轨独立音频在打架，专业感为零。

This module implements the "do it right" version, by construction:

* **Beat detection with stub fallback** — :class:`BeatTrackerProtocol`
  is the contract; the bundled :class:`StubBeatTracker` produces
  deterministic beats from BPM (no model, no I/O), :class:`MadmomBeatTracker`
  wraps the optional ``madmom`` package when installed.  Plugins/tests
  can pass any tracker that follows the protocol.
* **Ducking envelope** — :func:`compute_ducking_envelope` returns a
  per-millisecond gain map for the BGM that drops ``duck_db`` while
  voice is active and restores between sentences, with smoothed
  attack/release times so the gain change is not audible.
* **Beat-snapped trim** — :func:`snap_to_nearest_beat` clamps a target
  cut time to the nearest beat within ``tolerance_sec`` so loops /
  fades land on the downbeat (D2.8 — the whole point of madmom in
  CutClaw was this snap).
* **ffmpeg mixing** — :func:`build_ffmpeg_mix_command` returns the
  argv list (pure function, no subprocess), and :func:`mix_tracks`
  invokes it with a hard timeout (N1.4).  Splitting build vs. invoke
  means the test suite can assert on the exact filter graph without
  ever running ffmpeg.
* **D2.10 verification** — :func:`to_verification` flags problems the
  user should know about (BGM looped to fit foreground, ducking never
  triggered, beat-snap distance > 1 beat).

Public surface (the rest is private):

* :class:`Beat` / :class:`Sentence` / :class:`MixPlan` / :class:`MixResult`
* :class:`BeatTrackerProtocol` / :class:`StubBeatTracker` /
  :class:`MadmomBeatTracker`
* :func:`detect_voice_sentences_from_words`
* :func:`compute_ducking_envelope`
* :func:`snap_to_nearest_beat`
* :func:`plan_mix`
* :func:`build_ffmpeg_mix_command`
* :func:`mix_tracks`
* :func:`to_verification`

This file MUST NOT depend on FastAPI, on the plugin API or on the
host's brain.  It is pure logic so the same code can be used by
``plugin.py``, by CLI tools, and by other plugins.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from openakita_plugin_sdk.contrib import (
    KIND_NUMBER,
    KIND_OTHER,
    LowConfidenceField,
    Verification,
)

logger = logging.getLogger(__name__)


# ── ffmpeg helpers ────────────────────────────────────────────────────


# N1.4 — every subprocess.run MUST have a timeout.  These constants live
# here so the plugin layer cannot accidentally call ffmpeg without one.
DEFAULT_FFMPEG_MIX_TIMEOUT_SEC = 600.0  # plenty for any podcast-length mix


def ffmpeg_available() -> bool:
    """Return True only when ``ffmpeg`` is on PATH.

    We only need ffmpeg for the mix step (no ffprobe, beat detection
    runs on its own).  Returning False here lets the plugin emit a
    friendly "install ffmpeg" error rather than crashing.
    """
    return bool(shutil.which("ffmpeg"))


# ── primitives ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Beat:
    """One detected beat in the BGM timeline.

    Attributes:
        index: 0-based beat number.
        time_sec: Beat onset, in seconds from BGM start.
        downbeat: True if the tracker marked this as a measure
            downbeat (madmom's joint tracker does; the stub does
            ``every_n_beats == 4``).
    """

    index: int
    time_sec: float
    downbeat: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Sentence:
    """One contiguous voice segment in the foreground (used for ducking)."""

    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MixPlan:
    """Pure-data plan that :func:`mix_tracks` consumes.

    A plan can be inspected (cost preview, smoke testing) without
    touching ffmpeg.  Two plans for the same inputs MUST be equal —
    the planner is deterministic so re-runs reproduce the same mix.
    """

    voice_path: str
    bgm_path: str
    bgm_loop_count: int
    bgm_trim_start_sec: float
    bgm_trim_end_sec: float
    fade_in_sec: float
    fade_out_sec: float
    voice_gain_db: float
    bgm_gain_db: float
    duck_db: float
    duck_envelope: list[tuple[float, float]] = field(default_factory=list)  # (time_sec, gain_db)
    beats: list[Beat] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    bpm: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "voice_path": self.voice_path,
            "bgm_path": self.bgm_path,
            "bgm_loop_count": self.bgm_loop_count,
            "bgm_trim_start_sec": self.bgm_trim_start_sec,
            "bgm_trim_end_sec": self.bgm_trim_end_sec,
            "fade_in_sec": self.fade_in_sec,
            "fade_out_sec": self.fade_out_sec,
            "voice_gain_db": self.voice_gain_db,
            "bgm_gain_db": self.bgm_gain_db,
            "duck_db": self.duck_db,
            "duck_envelope": [list(p) for p in self.duck_envelope],
            "beats": [b.to_dict() for b in self.beats],
            "sentences": [s.to_dict() for s in self.sentences],
            "bpm": self.bpm,
            "notes": self.notes,
        }


@dataclass
class MixResult:
    """Outcome of running a :class:`MixPlan` through ffmpeg."""

    plan: MixPlan
    output_path: str
    duration_sec: float
    ffmpeg_cmd: list[str]
    used_madmom: bool
    voice_active_ratio: float = 0.0  # 0..1 share of timeline that has voice
    snap_max_distance_sec: float = 0.0  # worst beat-snap miss
    looped: bool = False  # BGM had to be repeated to cover voice

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "output_path": self.output_path,
            "duration_sec": self.duration_sec,
            "ffmpeg_cmd": list(self.ffmpeg_cmd),
            "used_madmom": self.used_madmom,
            "voice_active_ratio": self.voice_active_ratio,
            "snap_max_distance_sec": self.snap_max_distance_sec,
            "looped": self.looped,
        }


# ── beat tracker protocol + adapters ──────────────────────────────────


class BeatTrackerProtocol(Protocol):
    """Adapter contract for any beat-detection backend.

    Implementations MUST be deterministic given the same audio (so two
    runs cache equally) and MUST produce beats in increasing time
    order.  ``bpm`` is the average tempo over the whole track.
    """

    @property
    def tracker_id(self) -> str:  # pragma: no cover - protocol
        ...

    def detect(self, audio_path: Path, *, duration_sec: float) -> tuple[list[Beat], float]:  # pragma: no cover
        """Return (beats, bpm) for ``audio_path`` whose total length is
        ``duration_sec``.  Both are required so the stub can produce a
        valid timeline without re-probing the file."""
        ...


@dataclass
class StubBeatTracker:
    """Deterministic synthetic beats from a BPM hint.

    Used when:

    * madmom is not installed (the most common case — madmom is heavy
      and most users won't have it),
    * The plugin is running in "preview" mode where the user is just
      tuning ducking settings,
    * Unit tests need a fixed beat grid.

    The synthetic grid puts a beat every ``60/bpm`` seconds starting
    at ``offset_sec``; every 4th beat is marked ``downbeat=True`` so
    the snap helper has measure information just like madmom would
    provide.
    """

    bpm: float = 120.0
    offset_sec: float = 0.0
    beats_per_measure: int = 4

    @property
    def tracker_id(self) -> str:
        return "stub"

    def detect(self, audio_path: Path, *, duration_sec: float) -> tuple[list[Beat], float]:
        if self.bpm <= 0:
            raise ValueError(f"bpm must be > 0, got {self.bpm}")
        if duration_sec <= 0:
            return [], self.bpm
        period = 60.0 / self.bpm
        beats: list[Beat] = []
        i = 0
        t = self.offset_sec
        while t < duration_sec - 1e-6:
            beats.append(Beat(
                index=i,
                time_sec=t,
                downbeat=(i % max(1, self.beats_per_measure)) == 0,
            ))
            i += 1
            t = self.offset_sec + i * period
        return beats, self.bpm


@dataclass
class MadmomBeatTracker:
    """Adapter around the optional ``madmom`` package.

    We don't import madmom at module load time because most installs
    don't have it and it pulls a lot of native deps.  ``detect``
    raises ``RuntimeError`` if madmom is missing; the plugin layer
    falls back to :class:`StubBeatTracker` in that case.
    """

    fallback_bpm: float = 120.0  # used only if madmom can't infer one

    @property
    def tracker_id(self) -> str:
        return "madmom"

    def detect(self, audio_path: Path, *, duration_sec: float) -> tuple[list[Beat], float]:
        try:
            from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised only when madmom missing
            raise RuntimeError(
                "madmom is not installed; pip install madmom or use StubBeatTracker"
            ) from e
        try:
            act = RNNBeatProcessor()(str(audio_path))
            proc = DBNBeatTrackingProcessor(fps=100)
            times = list(proc(act))
        except Exception as e:  # pragma: no cover - madmom is opaque
            raise RuntimeError(f"madmom beat tracking failed: {e}") from e
        if not times:
            return [], self.fallback_bpm
        beats: list[Beat] = []
        for i, t in enumerate(times):
            beats.append(Beat(index=i, time_sec=float(t), downbeat=(i % 4 == 0)))
        # Average tempo from inter-beat intervals.
        if len(times) >= 2:
            avg_period = (times[-1] - times[0]) / (len(times) - 1)
            bpm = 60.0 / avg_period if avg_period > 0 else self.fallback_bpm
        else:
            bpm = self.fallback_bpm
        return beats, float(bpm)


# ── voice activity from words ──────────────────────────────────────────


# Sensible defaults: a "sentence" splits on >= 0.7 s gap, which is the
# same threshold transcribe-archive uses for its SRT cue grouping.  Using
# the same number means a transcript and a mix produced from it will
# have aligned cue boundaries — important for downstream tools (and
# matches what CutClaw's madmom pipeline expects).
DEFAULT_SENTENCE_GAP_SEC = 0.7
MIN_SENTENCE_DURATION_SEC = 0.2  # words shorter than this are joined to the next


def detect_voice_sentences_from_words(
    words: list[dict[str, Any]],
    *,
    gap_sec: float = DEFAULT_SENTENCE_GAP_SEC,
) -> list[Sentence]:
    """Group transcribed words into sentences for ducking.

    Pure function — input is whatever ``Word.to_dict()`` produces (so
    bgm-mixer is decoupled from transcribe-archive's exact dataclass).
    Each input dict must have ``start`` and ``end`` floats; ``text`` is
    not required.

    A new sentence starts whenever the inter-word gap exceeds
    ``gap_sec``.  Sentences shorter than ``MIN_SENTENCE_DURATION_SEC``
    are absorbed into the next one — single-word interjections like
    "嗯" should not trigger their own duck cycle.
    """
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: float(w["start"]))
    sentences: list[Sentence] = []
    cur_start = float(sorted_words[0]["start"])
    cur_end = float(sorted_words[0]["end"])
    for w in sorted_words[1:]:
        s = float(w["start"])
        e = float(w["end"])
        if s - cur_end > gap_sec:
            sentences.append(Sentence(start_sec=cur_start, end_sec=cur_end))
            cur_start = s
        cur_end = max(cur_end, e)
    sentences.append(Sentence(start_sec=cur_start, end_sec=cur_end))

    # Merge sentences shorter than the minimum into the following one.
    merged: list[Sentence] = []
    for s in sentences:
        if merged and merged[-1].duration_sec < MIN_SENTENCE_DURATION_SEC:
            prev = merged[-1]
            merged[-1] = Sentence(start_sec=prev.start_sec, end_sec=s.end_sec)
        else:
            merged.append(s)
    return merged


# ── ducking envelope ───────────────────────────────────────────────────


# CutClaw / AnyGen recommend -8 to -12 dB ducking; we default to -10 dB.
# Attack 60 ms (fast enough that voice is never clipped by BGM) and
# release 250 ms (long enough that the listener doesn't notice a "pump").
DEFAULT_DUCK_DB = -10.0
DEFAULT_DUCK_ATTACK_SEC = 0.06
DEFAULT_DUCK_RELEASE_SEC = 0.25


def compute_ducking_envelope(
    sentences: list[Sentence],
    *,
    total_duration_sec: float,
    duck_db: float = DEFAULT_DUCK_DB,
    attack_sec: float = DEFAULT_DUCK_ATTACK_SEC,
    release_sec: float = DEFAULT_DUCK_RELEASE_SEC,
) -> list[tuple[float, float]]:
    """Return a piecewise-linear gain envelope for the BGM track.

    Each tuple is ``(time_sec, gain_db_relative_to_bgm_gain_db)``.
    The first sample is at 0 with 0 dB; envelope goes to ``duck_db``
    over ``attack_sec`` before each sentence start, holds during the
    sentence, then ramps back over ``release_sec``.

    Pure function — produces a deterministic envelope ffmpeg's
    ``volume=`` filter (with ``eval=frame``) can interpolate.

    Edge cases:
    * No sentences → a flat ``[(0, 0)]`` envelope (BGM at full level).
    * Overlapping sentences → merged into one duck region.
    * ``total_duration_sec <= 0`` → empty envelope.
    """
    if total_duration_sec <= 0:
        return []
    if not sentences:
        return [(0.0, 0.0)]
    if duck_db > 0:
        raise ValueError(f"duck_db must be <= 0 (a cut), got {duck_db}")
    if attack_sec < 0 or release_sec < 0:
        raise ValueError("attack_sec / release_sec must be >= 0")

    # Merge overlapping / touching sentences.
    merged: list[Sentence] = []
    for s in sorted(sentences, key=lambda x: x.start_sec):
        if merged and s.start_sec <= merged[-1].end_sec:
            prev = merged[-1]
            merged[-1] = Sentence(
                start_sec=prev.start_sec,
                end_sec=max(prev.end_sec, s.end_sec),
            )
        else:
            merged.append(s)

    env: list[tuple[float, float]] = [(0.0, 0.0)]
    for s in merged:
        # Pre-attack ramp.
        attack_start = max(0.0, s.start_sec - attack_sec)
        if attack_start > env[-1][0]:
            env.append((attack_start, 0.0))
        env.append((s.start_sec, duck_db))
        env.append((s.end_sec, duck_db))
        release_end = min(total_duration_sec, s.end_sec + release_sec)
        env.append((release_end, 0.0))
    if env[-1][0] < total_duration_sec:
        env.append((total_duration_sec, 0.0))

    # Drop any redundant consecutive samples (same time, same gain).
    deduped: list[tuple[float, float]] = []
    for pt in env:
        if deduped and abs(pt[0] - deduped[-1][0]) < 1e-9 and abs(pt[1] - deduped[-1][1]) < 1e-9:
            continue
        deduped.append(pt)
    return deduped


def voice_active_ratio(
    sentences: list[Sentence], *, total_duration_sec: float,
) -> float:
    """Share of the timeline that is voice-active (0..1)."""
    if total_duration_sec <= 0:
        return 0.0
    active = sum(s.duration_sec for s in sentences)
    return min(1.0, max(0.0, active / total_duration_sec))


# ── beat snap ──────────────────────────────────────────────────────────


def snap_to_nearest_beat(
    target_sec: float,
    beats: list[Beat],
    *,
    tolerance_sec: float = 0.5,
    prefer_downbeat: bool = False,
) -> tuple[float, float]:
    """Snap ``target_sec`` to the nearest beat within ``tolerance_sec``.

    Returns ``(snapped_sec, distance_sec)`` — the second element is
    the *signed* miss (positive = beat is later than target).  If no
    beat is within tolerance, returns ``(target_sec, 0.0)`` so the
    caller can decide to keep the un-snapped value.

    When ``prefer_downbeat=True``, downbeats win over off-beats inside
    the tolerance window — the typical "loop the BGM at the top of a
    bar" use case.
    """
    if not beats:
        return target_sec, 0.0
    candidates: Iterable[Beat] = beats
    if prefer_downbeat:
        downs = [b for b in beats if b.downbeat]
        if downs:
            candidates = downs
    nearest = min(candidates, key=lambda b: abs(b.time_sec - target_sec))
    distance = nearest.time_sec - target_sec
    if abs(distance) > tolerance_sec:
        return target_sec, 0.0
    return nearest.time_sec, distance


# ── plan + mix ─────────────────────────────────────────────────────────


# Default fades.  D2.8 (CutClaw madmom table) uses 250 ms attack / 500 ms
# release for soundtrack joins.  We default to 300 ms / 500 ms — slightly
# slower in to avoid clipping, same release for consistency with CutClaw.
DEFAULT_FADE_IN_SEC = 0.3
DEFAULT_FADE_OUT_SEC = 0.5
DEFAULT_BGM_GAIN_DB = -3.0
DEFAULT_VOICE_GAIN_DB = 0.0


def plan_mix(
    *,
    voice_path: str | Path,
    bgm_path: str | Path,
    voice_duration_sec: float,
    bgm_duration_sec: float,
    sentences: list[Sentence],
    beats: list[Beat],
    bpm: float,
    duck_db: float = DEFAULT_DUCK_DB,
    duck_attack_sec: float = DEFAULT_DUCK_ATTACK_SEC,
    duck_release_sec: float = DEFAULT_DUCK_RELEASE_SEC,
    fade_in_sec: float = DEFAULT_FADE_IN_SEC,
    fade_out_sec: float = DEFAULT_FADE_OUT_SEC,
    voice_gain_db: float = DEFAULT_VOICE_GAIN_DB,
    bgm_gain_db: float = DEFAULT_BGM_GAIN_DB,
    snap_tolerance_sec: float = 0.5,
) -> MixPlan:
    """Build a deterministic :class:`MixPlan`.

    Decisions captured in the plan:

    * **Loop count**: ``ceil(voice_duration / bgm_duration)`` so the
      BGM always covers the voice; if BGM is longer than voice we
      trim instead of loop.
    * **Trim end**: snapped to the nearest BGM beat within tolerance
      so the loop join is musical (D2.8).
    * **Fade in/out**: capped to half the output duration so a 1 s
      short doesn't get a 500 ms fade in + 500 ms fade out and end up
      0 ms loud.
    * **Ducking envelope**: precomputed so the renderer (or a CLI
      preview) can show the gain curve before ffmpeg runs.

    Pure function — same inputs produce byte-identical plans.
    """
    if voice_duration_sec <= 0:
        raise ValueError(f"voice_duration_sec must be > 0, got {voice_duration_sec}")
    if bgm_duration_sec <= 0:
        raise ValueError(f"bgm_duration_sec must be > 0, got {bgm_duration_sec}")

    out_duration = voice_duration_sec
    if bgm_duration_sec >= voice_duration_sec:
        loop_count = 1
        snapped_end, _ = snap_to_nearest_beat(
            voice_duration_sec, beats,
            tolerance_sec=snap_tolerance_sec, prefer_downbeat=True,
        )
        trim_end = max(min(snapped_end, bgm_duration_sec), 0.5)
    else:
        loop_count = max(1, math.ceil(voice_duration_sec / bgm_duration_sec))
        # We'll loop the entire BGM file ``loop_count`` times then trim
        # to ``voice_duration_sec`` (or beat-snapped slightly past it).
        trim_end = voice_duration_sec
        snap_target = (
            voice_duration_sec
            - (loop_count - 1) * bgm_duration_sec  # remainder we'll need
        )
        # Snap the LAST loop's cut point to a beat for musicality.
        snapped, _ = snap_to_nearest_beat(
            snap_target, beats,
            tolerance_sec=snap_tolerance_sec, prefer_downbeat=True,
        )
        if snapped > 0:
            trim_end = (loop_count - 1) * bgm_duration_sec + snapped

    fade_in = max(0.0, min(fade_in_sec, out_duration / 2))
    fade_out = max(0.0, min(fade_out_sec, out_duration / 2))

    envelope = compute_ducking_envelope(
        sentences,
        total_duration_sec=out_duration,
        duck_db=duck_db,
        attack_sec=duck_attack_sec,
        release_sec=duck_release_sec,
    )

    return MixPlan(
        voice_path=str(voice_path),
        bgm_path=str(bgm_path),
        bgm_loop_count=loop_count,
        bgm_trim_start_sec=0.0,
        bgm_trim_end_sec=trim_end,
        fade_in_sec=fade_in,
        fade_out_sec=fade_out,
        voice_gain_db=voice_gain_db,
        bgm_gain_db=bgm_gain_db,
        duck_db=duck_db,
        duck_envelope=envelope,
        beats=list(beats),
        sentences=list(sentences),
        bpm=bpm,
        notes="" if loop_count == 1 else f"BGM looped {loop_count} times to cover voice",
    )


def _envelope_to_volume_expression(envelope: list[tuple[float, float]]) -> str:
    """Convert a piecewise envelope into a volume= filter expression.

    ffmpeg's ``volume=`` accepts a time-driven expression in dB via
    the ``volume`` parameter together with ``eval=frame``.  We build
    a nested ``if(lt(t,T1), V1, if(lt(t,T2), V2, ...))`` chain that
    interpolates linearly between segments — the result is a smooth
    duck/release ramp that ffmpeg evaluates per audio frame.

    Returns a string suitable for the ``volume=`` arg.  Pure function.
    """
    if not envelope:
        return "0"
    if len(envelope) == 1:
        return _db_to_amp_expr(envelope[0][1])
    parts: list[str] = []
    for (t1, v1), (t2, v2) in zip(envelope[:-1], envelope[1:]):
        if t2 <= t1:
            continue
        # Linear interpolation in dB → exp in amplitude (dB-domain
        # interpolation is the standard "log fade" listeners expect).
        slope = (v2 - v1) / (t2 - t1)
        parts.append(
            f"if(between(t,{t1:.4f},{t2:.4f}),"
            f"{_db_to_amp_expr(f'{v1:.4f}+({slope:.4f})*(t-{t1:.4f})')},"
        )
    last_v = envelope[-1][1]
    expr = "".join(parts) + _db_to_amp_expr(last_v) + (")" * len(parts))
    return expr


def _db_to_amp_expr(db: float | str) -> str:
    """Render a dB value (constant or expr) as ffmpeg's ``pow(10, dB/20)``.

    We use ``pow`` (== ``10^x``) rather than ``exp`` because ffmpeg's
    expression evaluator interprets ``pow(x, y)`` as ``x^y`` — checked
    against ffmpeg 6.x docs.
    """
    return f"pow(10,({db})/20)"


def build_ffmpeg_mix_command(
    plan: MixPlan, *, output_path: str | Path,
) -> list[str]:
    """Translate a :class:`MixPlan` into an ffmpeg argv list.

    Pure function — does NOT invoke subprocess.  The plan + this
    builder being pure means the test suite can assert on the exact
    argv (including the filter graph) without ever touching the
    system, and the plugin can preview the command in a "show me
    what you'll run" UI.

    The filter graph is:

        [bgm] aloop=loop=N -> atrim -> volume(envelope) -> afade
                                                    \
        [voice] volume(voice_gain)                    -> amix -> out
                                                    /
    """
    out = str(output_path)
    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    cmd.extend(["-i", plan.voice_path])
    cmd.extend(["-i", plan.bgm_path])

    voice_amp = _db_to_amp_expr(plan.voice_gain_db)
    bgm_base_amp = _db_to_amp_expr(plan.bgm_gain_db)
    duck_expr = _envelope_to_volume_expression(plan.duck_envelope)

    # Loop the BGM if needed.  ``aloop=loop=N`` repeats N+1 times when N
    # > 0; we want exactly ``bgm_loop_count`` plays so use N = count-1.
    loop_n = max(0, plan.bgm_loop_count - 1)
    bgm_chain = (
        f"[1:a]aloop=loop={loop_n}:size=2147483647,"
        f"atrim=start={plan.bgm_trim_start_sec:.4f}:end={plan.bgm_trim_end_sec:.4f},"
        f"asetpts=PTS-STARTPTS,"
        f"volume=volume={bgm_base_amp}*({duck_expr}):eval=frame,"
        f"afade=t=in:st=0:d={plan.fade_in_sec:.4f},"
        f"afade=t=out:st={max(0.0, plan.bgm_trim_end_sec - plan.fade_out_sec):.4f}:d={plan.fade_out_sec:.4f}"
        f"[bgm]"
    )
    voice_chain = f"[0:a]volume=volume={voice_amp}[voice]"
    mix = "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0[out]"

    filter_complex = ";".join([voice_chain, bgm_chain, mix])
    cmd.extend(["-filter_complex", filter_complex])
    cmd.extend(["-map", "[out]"])
    cmd.extend(["-c:a", "libmp3lame", "-q:a", "2", out])
    return cmd


def mix_tracks(
    plan: MixPlan,
    *,
    output_path: str | Path,
    timeout_sec: float = DEFAULT_FFMPEG_MIX_TIMEOUT_SEC,
) -> MixResult:
    """Render ``plan`` to disk via ffmpeg.

    Hard timeout (N1.4) — never block forever even on a giant file.
    Returns a :class:`MixResult` whose ``ffmpeg_cmd`` matches what we
    actually invoked (good for replay / debugging).
    """
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not on PATH — install ffmpeg or use the dry-run "
            "API (build_ffmpeg_mix_command) for tests"
        )
    cmd = build_ffmpeg_mix_command(plan, output_path=output_path)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg mix timed out after {timeout_sec}s"
        ) from e
    except subprocess.CalledProcessError as e:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg mix failed: {(e.stderr or '').strip()[:300]}"
        ) from e

    snap_max = _max_snap_distance(plan)
    return MixResult(
        plan=plan,
        output_path=str(out_path),
        duration_sec=plan.bgm_trim_end_sec - plan.bgm_trim_start_sec,
        ffmpeg_cmd=cmd,
        used_madmom=False,  # caller patches if MadmomBeatTracker was used
        voice_active_ratio=voice_active_ratio(
            plan.sentences,
            total_duration_sec=plan.bgm_trim_end_sec - plan.bgm_trim_start_sec,
        ),
        snap_max_distance_sec=snap_max,
        looped=plan.bgm_loop_count > 1,
    )


def _max_snap_distance(plan: MixPlan) -> float:
    """Worst beat-snap distance across the plan (used by verification)."""
    if not plan.beats:
        return 0.0
    interest_points = [plan.bgm_trim_end_sec]
    worst = 0.0
    for tp in interest_points:
        nearest = min(plan.beats, key=lambda b: abs(b.time_sec - tp))
        worst = max(worst, abs(nearest.time_sec - tp))
    return worst


# ── verification (D2.10) ──────────────────────────────────────────────


# Thresholds for the verification badge.  They mirror the AnyGen
# yellow-highlight philosophy: the badge stays GREEN when the mix is
# clean, YELLOW when there's a non-fatal compromise (BGM had to loop,
# beat snap was off by > 1 beat at 120 bpm == 0.5 s) and RED is reserved
# for outright failures (engine throws, ffmpeg returns non-zero).
SNAP_DISTANCE_WARN_SEC = 0.25  # half a beat at 120 bpm
LOW_VOICE_RATIO_WARN = 0.05  # mix is "BGM-only" if < 5 % voice


def to_verification(result: MixResult) -> Verification:
    """Translate a :class:`MixResult` into a D2.10 envelope.

    Flagging rules:

    * BGM looped to cover voice → KIND_OTHER on ``$.mix.loop`` so the
      UI surfaces "我把 BGM 循环了 N 次".
    * Beat-snap distance > ``SNAP_DISTANCE_WARN_SEC`` → KIND_NUMBER on
      ``$.mix.snap`` (musicality compromise, listener may notice).
    * Voice activity < ``LOW_VOICE_RATIO_WARN`` → KIND_OTHER on
      ``$.mix.duck`` (ducking effectively never triggered; user may
      have given the wrong files).

    ``verifier_id`` is fixed so future composition with a real second-
    model verifier merges cleanly via :func:`merge_verifications`.
    """
    fields: list[LowConfidenceField] = []
    if result.looped:
        fields.append(LowConfidenceField(
            path="$.mix.loop",
            value=f"x{result.plan.bgm_loop_count}",
            kind=KIND_OTHER,
            reason=f"BGM 循环了 {result.plan.bgm_loop_count} 次以覆盖人声时长",
        ))
    if result.snap_max_distance_sec > SNAP_DISTANCE_WARN_SEC:
        fields.append(LowConfidenceField(
            path="$.mix.snap",
            value=f"{result.snap_max_distance_sec:.2f}s",
            kind=KIND_NUMBER,
            reason=f"剪辑点偏离最近节拍 {result.snap_max_distance_sec:.2f}s (>0.25s)",
        ))
    if result.voice_active_ratio < LOW_VOICE_RATIO_WARN and result.plan.sentences:
        fields.append(LowConfidenceField(
            path="$.mix.duck",
            value=f"{result.voice_active_ratio*100:.1f}%",
            kind=KIND_NUMBER,
            reason="人声占比过低，ducking 几乎未生效",
        ))
    notes_bits = []
    if result.used_madmom:
        notes_bits.append("madmom beat tracker")
    else:
        notes_bits.append("stub beat tracker")
    if result.looped:
        notes_bits.append(f"BGM × {result.plan.bgm_loop_count}")
    return Verification(
        verified=not fields,
        verifier_id="bgm_mixer_self_check",
        low_confidence_fields=fields,
        notes="; ".join(notes_bits),
    )


__all__ = [
    "Beat",
    "BeatTrackerProtocol",
    "DEFAULT_DUCK_ATTACK_SEC",
    "DEFAULT_DUCK_DB",
    "DEFAULT_DUCK_RELEASE_SEC",
    "DEFAULT_FADE_IN_SEC",
    "DEFAULT_FADE_OUT_SEC",
    "DEFAULT_FFMPEG_MIX_TIMEOUT_SEC",
    "DEFAULT_SENTENCE_GAP_SEC",
    "LOW_VOICE_RATIO_WARN",
    "MIN_SENTENCE_DURATION_SEC",
    "MadmomBeatTracker",
    "MixPlan",
    "MixResult",
    "SNAP_DISTANCE_WARN_SEC",
    "Sentence",
    "StubBeatTracker",
    "build_ffmpeg_mix_command",
    "compute_ducking_envelope",
    "detect_voice_sentences_from_words",
    "ffmpeg_available",
    "mix_tracks",
    "plan_mix",
    "snap_to_nearest_beat",
    "to_verification",
    "voice_active_ratio",
]
