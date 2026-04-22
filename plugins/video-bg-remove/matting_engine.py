"""video-bg-remove —pure-logic core.

This module wraps RVM (`Robust Video Matting`_) MobileNetV3 via
``onnxruntime``.  RVM is a 4-input recurrent network: at each frame
we feed the source image plus the 4 recurrent state tensors from the
previous frame, and the network emits the foreground RGB, an alpha
matte, and the next-frame state tensors.

Heavy / non-deterministic dependencies are deliberately *lazy*:

* ``numpy`` —required for any real run; tests that don't run the
  actual pipeline never import it.
* ``onnxruntime`` —only loaded when ``load_rvm_session`` is called.
  This lets the plugin's tests pass on CI runners that lack the
  package, and lets the plugin module import cleanly so the host can
  serve the "install onnxruntime" guidance route.
* The 100 MB model file lives in the plugin's data dir, not in the
  repo.  ``model_available`` checks for it; the host UI handles
  download via the SDK's :class:`DependencyGate` patterns.

Keeping this layer thin (no FastAPI, no asyncio, no sqlite) means the
same engine can be exercised inside ``shorts-batch`` (D3) when it
needs background removal as one stage of a longer pipeline.

.. _Robust Video Matting: https://github.com/PeterL1n/RobustVideoMatting
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from openakita_plugin_sdk.contrib import (
    KIND_NUMBER,
    KIND_OTHER,
    LowConfidenceField,
    Verification,
    ffprobe_json_sync,
)

__all__ = [
    "DEFAULT_DOWNSAMPLE_RATIO",
    "DEFAULT_FPS",
    "DEFAULT_MODEL_FILENAME",
    "DEFAULT_PROBE_TIMEOUT_SEC",
    "DEFAULT_RENDER_TIMEOUT_SEC",
    "Background",
    "MattingPlan",
    "MattingResult",
    "build_default_background",
    "composite_frame",
    "ffmpeg_available",
    "iter_video_frames",
    "load_rvm_session",
    "model_available",
    "onnxruntime_available",
    "parse_color",
    "plan_matting",
    "probe_video_meta",
    "rgba_to_rgb_over",
    "run_matting",
    "to_verification",
]


# 鈹€鈹€ Constants 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


DEFAULT_FPS = 25.0
# 0.25 is the official RVM recommendation for 1080p sources —runs at
# real-time on CPU MobileNetV3 with negligible quality loss.  Smaller
# sources (e.g. 480p) can use 0.5; 4K can drop to 0.125.
DEFAULT_DOWNSAMPLE_RATIO = 0.25
DEFAULT_PROBE_TIMEOUT_SEC = 60.0
DEFAULT_RENDER_TIMEOUT_SEC = 1800.0  # 30 min hard ceiling
DEFAULT_MODEL_FILENAME = "rvm_mobilenetv3_fp32.onnx"


# 鈹€鈹€ Background descriptor 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


@dataclass(frozen=True)
class Background:
    """What to paint behind the matted foreground.

    ``kind`` is the discriminator:

    * ``"color"``       —solid RGB, ``color`` tuple required.
    * ``"image"``       —still image stretched to cover, ``image_path``
      required (must exist; otherwise validation raises).
    * ``"transparent"`` —emit RGBA, no compositing.  Output container
      forced to ``.mov`` (libx264 doesn't support alpha).
    """

    kind: str
    color: tuple[int, int, int] | None = None
    image_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "color": list(self.color) if self.color else None,
            "image_path": self.image_path,
        }


def build_default_background() -> Background:
    """Standard default —chroma-key green so the user can re-key in DaVinci."""
    return Background(kind="color", color=(0, 177, 64))


def parse_color(value: Any) -> tuple[int, int, int]:
    """Accept ``[r,g,b]``, ``"#rrggbb"``, ``"rrggbb"``, or ``"r,g,b"``.

    Returns a clamped (0-255) RGB tuple; raises ``ValueError`` for
    anything else.  Centralising this means the API and brain-tool
    paths share one parser —no chance of one accepting hex and the
    other only accepting tuples.
    """
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(max(0, min(255, c))) for c in value)  # type: ignore[return-value]
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        if "," in s:
            parts = [p.strip() for p in s.split(",")]
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                return tuple(int(max(0, min(255, int(p)))) for p in parts)  # type: ignore[return-value]
    raise ValueError(
        f"unsupported color value: {value!r}; "
        "use [r,g,b], '#rrggbb', or 'r,g,b'"
    )


# 鈹€鈹€ Plan & result models 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


@dataclass
class MattingPlan:
    """Frozen description of what the worker will produce."""

    input_path: str
    output_path: str
    background: Background
    fps: float
    width: int
    height: int
    duration_sec: float
    model_path: str
    downsample_ratio: float = DEFAULT_DOWNSAMPLE_RATIO
    crf: int = 18
    libx264_preset: str = "fast"

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_path": self.input_path,
            "output_path": self.output_path,
            "background": self.background.to_dict(),
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration_sec": self.duration_sec,
            "model_path": self.model_path,
            "downsample_ratio": self.downsample_ratio,
            "crf": self.crf,
            "libx264_preset": self.libx264_preset,
        }


@dataclass
class MattingResult:
    """What the worker produced."""

    plan: MattingPlan
    output_path: str
    elapsed_sec: float
    frame_count: int
    output_size_bytes: int
    mean_alpha: float = 0.0   # 0.0 = nothing matted, 1.0 = full coverage

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "output_path": self.output_path,
            "elapsed_sec": self.elapsed_sec,
            "frame_count": self.frame_count,
            "output_size_bytes": self.output_size_bytes,
            "mean_alpha": self.mean_alpha,
        }


# 鈹€鈹€ Dep / runtime helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def onnxruntime_available() -> bool:
    """``True`` iff ``onnxruntime`` (or onnxruntime-gpu) can be imported."""
    return importlib.util.find_spec("onnxruntime") is not None


def model_available(model_path: str | Path) -> bool:
    p = Path(model_path)
    return p.is_file() and p.stat().st_size > 1_000_000  # >1 MB sanity


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def load_rvm_session(model_path: str | Path, *, providers: list[str] | None = None):
    """Construct an ``onnxruntime.InferenceSession`` for the RVM model.

    Defaults to ``CPUExecutionProvider`` so the plugin runs on every
    machine; callers wanting GPU should pass
    ``["CUDAExecutionProvider", "CPUExecutionProvider"]`` (the second
    is the fallback).

    Raises:
        ImportError: if onnxruntime is not installed.
        FileNotFoundError: if the model file is missing.
    """
    if not onnxruntime_available():
        raise ImportError(
            "onnxruntime is not installed. Install with `pip install "
            "onnxruntime` (CPU) or `pip install onnxruntime-gpu` (GPU)."
        )
    p = Path(model_path)
    if not p.is_file():
        raise FileNotFoundError(
            f"RVM model not found at {p}. Download from "
            "https://github.com/PeterL1n/RobustVideoMatting/releases "
            "(file: rvm_mobilenetv3_fp32.onnx)."
        )
    import onnxruntime as ort  # local import —see module docstring

    return ort.InferenceSession(str(p), providers=providers or ["CPUExecutionProvider"])


def probe_video_meta(input_path: str, *, timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC) -> dict[str, Any]:
    """Return ``{fps, width, height, duration_sec}`` via ``ffprobe``.

    Falls back to safe defaults when the probe fails so the caller
    doesn't have to second-guess.  Real failures (missing file) are
    still propagated by the calling worker via :func:`run_matting`.
    """
    try:
        info = ffprobe_json_sync(input_path, timeout_sec=timeout_sec)
    except Exception:  # noqa: BLE001 —defensive: any probe failure 鈫?defaults
        return {"fps": DEFAULT_FPS, "width": 0, "height": 0, "duration_sec": 0.0}

    streams = info.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        return {"fps": DEFAULT_FPS, "width": 0, "height": 0, "duration_sec": 0.0}
    s = video_streams[0]

    fps_raw = s.get("avg_frame_rate") or s.get("r_frame_rate") or "25/1"
    try:
        num, _, denom = fps_raw.partition("/")
        fps = float(num) / float(denom or 1) if denom else float(num)
        if fps <= 0:
            fps = DEFAULT_FPS
    except (ValueError, ZeroDivisionError):
        fps = DEFAULT_FPS

    width = int(s.get("width") or 0)
    height = int(s.get("height") or 0)
    duration_sec = 0.0
    fmt = info.get("format") or {}
    if fmt.get("duration"):
        try:
            duration_sec = float(fmt["duration"])
        except ValueError:
            duration_sec = 0.0
    return {"fps": fps, "width": width, "height": height, "duration_sec": duration_sec}


# 鈹€鈹€ Frame I/O & compositing 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def iter_video_frames(input_path: str, *, fps: float, width: int, height: int):
    """Yield raw RGB frames (numpy arrays, shape ``[H, W, 3]``) from ``input_path``.

    Pipes ``ffmpeg`` through stdout in ``rawvideo`` format —much
    faster and disk-cheaper than dumping PNGs.  We deliberately fix
    ``width`` / ``height`` to the source dims so the caller always
    knows the buffer shape.
    """
    import numpy as np

    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-i", str(input_path),
        "-vf", f"fps={fps}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = width * height * 3
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)  # type: ignore[union-attr]
            if not buf or len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait(timeout=5)


def composite_frame(fgr_rgb: Any, alpha: Any, background: Background) -> Any:
    """Blend ``fgr_rgb * alpha + bg * (1 - alpha)`` 鈫?``[H, W, 3]`` uint8.

    For ``transparent`` backgrounds we return RGBA; the caller writes
    that to a ``.mov`` (libx264 has no alpha).  ``image`` backgrounds
    expect ``background.image_path`` to point to an already-loaded /
    pre-resized RGB array path; for unit-test simplicity we read &
    cover-resize on every call (the worker caches outside this fn).
    """
    import numpy as np

    if alpha.ndim == 2:
        alpha = alpha[..., None]  # broadcast over RGB
    alpha = alpha.astype(np.float32).clip(0.0, 1.0)
    fgr = fgr_rgb.astype(np.float32)

    if background.kind == "transparent":
        # RGBA: pack alpha as the 4th channel (0-255).
        a8 = (alpha[..., 0] * 255.0).clip(0, 255).astype(np.uint8)
        out = np.empty((fgr.shape[0], fgr.shape[1], 4), dtype=np.uint8)
        out[..., :3] = fgr.clip(0, 255).astype(np.uint8)
        out[..., 3] = a8
        return out

    if background.kind == "color":
        bg_rgb = np.array(background.color or (0, 0, 0), dtype=np.float32)
        bg = np.broadcast_to(bg_rgb, fgr.shape)
    elif background.kind == "image":
        bg = _load_bg_image(background.image_path or "", fgr.shape[1], fgr.shape[0])
    else:
        raise ValueError(f"unsupported background.kind: {background.kind!r}")

    blended = fgr * alpha + bg.astype(np.float32) * (1.0 - alpha)
    return blended.clip(0, 255).astype(np.uint8)


def rgba_to_rgb_over(rgba: Any, *, bg_color: tuple[int, int, int] = (0, 0, 0)) -> Any:
    """Flatten an RGBA frame onto an opaque ``bg_color``.

    Useful when writing a debug preview to PNG (most viewers handle
    RGBA badly) or when a downstream pipeline only accepts RGB.
    """
    import numpy as np

    rgb = rgba[..., :3].astype(np.float32)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    bg = np.broadcast_to(np.array(bg_color, dtype=np.float32), rgb.shape)
    out = rgb * a + bg * (1.0 - a)
    return out.clip(0, 255).astype(np.uint8)


def _load_bg_image(image_path: str, width: int, height: int):
    """Load + cover-resize a background image to ``[H, W, 3]`` uint8.

    Pillow is a soft dep —every other media plugin already imports it
    (poster-maker, smart-poster-grid) so on a real install it will be
    present.  We fail loud if it's missing rather than silently
    falling back to black.
    """
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover —Pillow ships with Pillow-bound plugins
        raise ImportError(
            "background.kind='image' requires Pillow; "
            "install with `pip install pillow`."
        ) from e
    import numpy as np

    img = Image.open(image_path).convert("RGB")
    iw, ih = img.size
    scale = max(width / iw, height / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - width) // 2
    top = (nh - height) // 2
    img = img.crop((left, top, left + width, top + height))
    return np.asarray(img, dtype=np.uint8)


# 鈹€鈹€ Plan & run 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def plan_matting(
    *,
    input_path: str,
    output_path: str,
    background: Background | dict[str, Any] | None = None,
    model_path: str,
    fps: float | None = None,
    downsample_ratio: float = DEFAULT_DOWNSAMPLE_RATIO,
    crf: int = 18,
    libx264_preset: str = "fast",
    probe_timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC,
) -> MattingPlan:
    """Validate inputs, probe the source, freeze a :class:`MattingPlan`."""
    if not input_path or not str(input_path).strip():
        raise ValueError("input_path is required and must not be empty")
    if not output_path or not str(output_path).strip():
        raise ValueError("output_path is required and must not be empty")
    if not (0.05 <= downsample_ratio <= 1.0):
        raise ValueError(
            f"downsample_ratio must be in [0.05, 1.0], got {downsample_ratio!r}",
        )

    bg = _coerce_background(background)
    if bg.kind == "transparent" and not str(output_path).lower().endswith(".mov"):
        # libx264 has no alpha; force the caller to a .mov container so
        # the alpha channel survives.  Easier to fail fast here than
        # let ffmpeg silently drop the alpha 5 minutes into a render.
        raise ValueError(
            "background.kind='transparent' requires an output_path "
            "ending in .mov (libx264 cannot encode alpha; use prores/png).",
        )

    meta = probe_video_meta(input_path, timeout_sec=probe_timeout_sec)
    return MattingPlan(
        input_path=str(input_path),
        output_path=str(output_path),
        background=bg,
        fps=float(fps if fps is not None else meta["fps"]),
        width=int(meta["width"]),
        height=int(meta["height"]),
        duration_sec=float(meta["duration_sec"]),
        model_path=str(model_path),
        downsample_ratio=float(downsample_ratio),
        crf=int(crf),
        libx264_preset=str(libx264_preset),
    )


def _coerce_background(value: Any) -> Background:
    if value is None:
        return build_default_background()
    if isinstance(value, Background):
        return value
    if isinstance(value, dict):
        kind = (value.get("kind") or "color").lower()
        if kind == "color":
            color = parse_color(value.get("color") or [0, 177, 64])
            return Background(kind="color", color=color)
        if kind == "image":
            ip = value.get("image_path")
            if not ip or not Path(ip).is_file():
                raise ValueError(
                    f"background.kind='image' requires an existing image_path; "
                    f"got {ip!r}",
                )
            return Background(kind="image", image_path=str(ip))
        if kind == "transparent":
            return Background(kind="transparent")
        raise ValueError(f"unsupported background.kind: {kind!r}")
    raise TypeError(f"background must be a Background / dict / None, got {type(value).__name__}")


def run_matting(
    plan: MattingPlan,
    *,
    session_factory=None,
    on_progress=None,
    write_frame=None,
) -> MattingResult:
    """Execute a :class:`MattingPlan` and return the produced
    :class:`MattingResult`.

    The function is synchronous on purpose —``plugin.py`` calls it
    inside :func:`asyncio.to_thread` so the event loop stays
    responsive.  ``session_factory`` and ``write_frame`` are injection
    points so tests can short-circuit the heavy ffmpeg / onnxruntime
    parts.

    Args:
        session_factory: Callable returning the RVM session (defaults
            to :func:`load_rvm_session`).  Tests pass a fake.
        on_progress: ``Callable[[done, total], None]``.  Called once
            per frame; total may be ``None`` if duration was unknown.
        write_frame: ``Callable[[ndarray], None]``.  Called once per
            composited frame.  Default uses an internal ffmpeg pipe.
    """
    import time as _time

    import numpy as np

    started = _time.monotonic()
    session = (session_factory or load_rvm_session)(plan.model_path)

    # Pre-allocate the recurrent state inputs as zeros —RVM expects
    # 4 hidden tensors that carry temporal context between frames.
    rec = [np.zeros([1, 1, 1, 1], dtype=np.float32) for _ in range(4)]

    out_dir = Path(plan.output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    sink = write_frame
    sink_proc = None
    if sink is None:
        sink, sink_proc = _open_ffmpeg_writer(plan)

    frame_count = 0
    alpha_sum = 0.0
    total_frames = int(plan.duration_sec * plan.fps) if plan.duration_sec > 0 else None

    try:
        for frame in iter_video_frames(
            plan.input_path,
            fps=plan.fps, width=plan.width, height=plan.height,
        ):
            # Convert to NCHW float32 in [0, 1].
            src = frame.astype(np.float32) / 255.0
            src = np.transpose(src, (2, 0, 1))[None, ...]
            ds = np.array([plan.downsample_ratio], dtype=np.float32)

            outs = session.run(
                None,
                {
                    "src": src,
                    "r1i": rec[0], "r2i": rec[1],
                    "r3i": rec[2], "r4i": rec[3],
                    "downsample_ratio": ds,
                },
            )
            fgr_chw, pha_chw = outs[0], outs[1]
            rec = list(outs[2:6])

            fgr_rgb = (np.transpose(fgr_chw[0], (1, 2, 0)) * 255.0)
            alpha = pha_chw[0, 0]  # [H, W]
            composed = composite_frame(fgr_rgb, alpha, plan.background)

            sink(composed)
            frame_count += 1
            alpha_sum += float(alpha.mean())

            if on_progress is not None:
                on_progress(frame_count, total_frames)
    finally:
        if sink_proc is not None:
            _close_ffmpeg_writer(sink_proc, plan)

    elapsed = _time.monotonic() - started
    out_path = Path(plan.output_path)
    size_bytes = out_path.stat().st_size if out_path.is_file() else 0
    mean_alpha = (alpha_sum / frame_count) if frame_count else 0.0

    return MattingResult(
        plan=plan,
        output_path=str(out_path),
        elapsed_sec=elapsed,
        frame_count=frame_count,
        output_size_bytes=size_bytes,
        mean_alpha=mean_alpha,
    )


def _open_ffmpeg_writer(plan: MattingPlan):
    """Open an ffmpeg subprocess that consumes raw frames on stdin."""
    is_rgba = plan.background.kind == "transparent"
    pix_fmt_in = "rgba" if is_rgba else "rgb24"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", pix_fmt_in,
        "-s", f"{plan.width}x{plan.height}",
        "-r", str(plan.fps),
        "-i", "-",  # video frames from stdin
        # source for the audio stream (best-effort; -map drops if no audio)
        "-i", plan.input_path,
        "-map", "0:v:0",
        "-map", "1:a:0?",
    ]
    if is_rgba:
        cmd += ["-c:v", "qtrle"]  # alpha-preserving codec for .mov
    else:
        cmd += [
            "-c:v", "libx264",
            "-preset", plan.libx264_preset,
            "-crf", str(plan.crf),
            "-pix_fmt", "yuv420p",
        ]
    cmd += ["-c:a", "aac", "-shortest", str(plan.output_path)]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _write(arr) -> None:
        if proc.stdin:
            proc.stdin.write(arr.tobytes())

    return _write, proc


def _close_ffmpeg_writer(proc, plan: MattingPlan) -> None:
    if proc.stdin:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    try:
        proc.wait(timeout=DEFAULT_RENDER_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise


# 鈹€鈹€ Verification (D2.10) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def to_verification(result: MattingResult) -> Verification:
    """Convert a :class:`MattingResult` into a D2.10 verification envelope."""
    fields: list[LowConfidenceField] = []

    if result.frame_count == 0:
        fields.append(LowConfidenceField(
            path="$.frame_count",
            value=0,
            kind=KIND_NUMBER,
            reason="no frames were produced —likely an ffmpeg decode "
                   "error or the source is 0-length",
        ))

    # < 1% mean alpha typically means the matting net found nothing
    # human-shaped (empty room, abstract footage) —still ship the
    # output but flag for human review.
    if 0 < result.mean_alpha < 0.01:
        fields.append(LowConfidenceField(
            path="$.mean_alpha",
            value=result.mean_alpha,
            kind=KIND_NUMBER,
            reason="mean alpha is below 1% —the matting model probably "
                   "did not find a person in the frame; verify visually",
        ))

    if result.output_size_bytes == 0 and result.plan.background.kind != "transparent":
        fields.append(LowConfidenceField(
            path="$.output_size_bytes",
            value=0,
            kind=KIND_NUMBER,
            reason="output file is 0 bytes —the ffmpeg writer probably "
                   "failed silently; check disk space and codecs",
        ))

    if result.plan.duration_sec > 0 and result.frame_count > 0:
        # Detect "we rendered way fewer frames than the source" —common
        # when ffmpeg crashes mid-stream and our writer flushed early.
        expected = result.plan.duration_sec * result.plan.fps
        actual = result.frame_count
        if expected > 0 and abs(actual - expected) / expected > 0.10:
            fields.append(LowConfidenceField(
                path="$.frame_count",
                value=actual,
                kind=KIND_NUMBER,
                reason=(
                    f"rendered {actual} frames but expected ~{int(expected)} "
                    f"({plan_duration_repr(result.plan.duration_sec)} "
                    f"@ {result.plan.fps} fps); the source may have variable "
                    "frame-rate or the render was truncated"
                ),
            ))

    return Verification(
        verified=not fields,
        verifier_id="video_bg_remove_self_check",
        low_confidence_fields=fields,
    )


def plan_duration_repr(d: float) -> str:
    """Render seconds as ``"M:SS"`` for human-readable verification flags."""
    if d <= 0:
        return "0:00"
    m, s = divmod(int(round(d)), 60)
    return f"{m}:{s:02d}"
