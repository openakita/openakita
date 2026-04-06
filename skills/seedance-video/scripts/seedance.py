#!/usr/bin/env python3
"""Seedance Video Generation CLI — dual-backend support (Volcengine Ark & EvoLink 2.0)."""

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Backend configuration
# ---------------------------------------------------------------------------

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
EVOLINK_CREATE = "https://api.evolink.ai/v1/videos/generations"
EVOLINK_STATUS = "https://api.evolink.ai/v1/tasks"

ARK_MODELS = [
    "doubao-seedance-1-5-pro-251215",
    "doubao-seedance-1-0-pro-250428",
    "doubao-seedance-1-0-lite-t2v-250219",
    "doubao-seedance-1-0-lite-i2v-250219",
]
ARK_DEFAULT_MODEL = ARK_MODELS[0]

EVOLINK_MODELS = [
    "seedance-2.0-text-to-video",
    "seedance-2.0-image-to-video",
    "seedance-2.0-reference-to-video",
]

ALL_MODELS = ARK_MODELS + EVOLINK_MODELS

RATIOS = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"]
RESOLUTIONS = ["480p", "720p", "1080p"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _resolve_backend():
    """Return (backend, api_key) based on env vars."""
    ark = os.environ.get("ARK_API_KEY")
    evo = os.environ.get("EVOLINK_API_KEY")
    if ark:
        return "ark", ark
    if evo:
        return "evolink", evo
    return None, None


def _image_to_data_url(path_or_url: str) -> str:
    """Convert a local file path to a base64 data-URL; URLs pass through."""
    if path_or_url.startswith(("http://", "https://", "data:")):
        return path_or_url
    p = Path(path_or_url).expanduser().resolve()
    if not p.is_file():
        _die(f"Image file not found: {path_or_url}")
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _http(method: str, url: str, api_key: str, body: dict | None = None) -> dict:
    """Minimal HTTP helper using stdlib only."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode() if exc.fp else ""
        _die(f"HTTP {exc.code} — {err_body}")
    except urllib.error.URLError as exc:
        _die(f"Network error — {exc.reason}")
    return {}  # unreachable


# ---------------------------------------------------------------------------
# Ark API helpers
# ---------------------------------------------------------------------------


def _ark_build_content(args) -> list[dict]:
    """Build the Ark-style content array from CLI args."""
    content: list[dict] = []
    if args.prompt:
        content.append({"type": "text", "text": args.prompt})
    if args.image:
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_to_data_url(args.image)},
            "role": "first_frame",
        })
    if args.last_frame:
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_to_data_url(args.last_frame)},
            "role": "last_frame",
        })
    if args.ref_images:
        for img in args.ref_images:
            content.append({
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(img)},
            })
    return content


def _ark_create(args, api_key: str) -> dict:
    model = args.model or ARK_DEFAULT_MODEL
    payload: dict = {
        "model": model,
        "content": _ark_build_content(args),
    }
    if args.ratio:
        payload["ratio"] = args.ratio
    if args.duration:
        payload["duration"] = args.duration
    if args.resolution:
        payload["resolution"] = args.resolution
    if args.seed is not None:
        payload["seed"] = args.seed
    if args.camera_fixed:
        payload["camera_fixed"] = True
    if args.watermark:
        payload["watermark"] = True
    if args.generate_audio is not None:
        payload["generate_audio"] = args.generate_audio
    if args.draft:
        payload["draft"] = True
    if args.return_last_frame:
        payload["return_last_frame"] = True
    return _http("POST", ARK_BASE, api_key, payload)


def _ark_status(task_id: str, api_key: str) -> dict:
    return _http("GET", f"{ARK_BASE}/{task_id}", api_key)


def _ark_list(api_key: str, page: int = 1, size: int = 10) -> dict:
    url = f"{ARK_BASE}?page_num={page}&page_size={size}"
    return _http("GET", url, api_key)


def _ark_delete(task_id: str, api_key: str) -> dict:
    return _http("DELETE", f"{ARK_BASE}/{task_id}", api_key)


# ---------------------------------------------------------------------------
# EvoLink API helpers
# ---------------------------------------------------------------------------


def _evolink_pick_model(args) -> str:
    """Auto-select EvoLink 2.0 model from inputs."""
    if args.model and args.model in EVOLINK_MODELS:
        return args.model
    has_video = bool(args.video)
    has_audio = bool(args.audio)
    n_images = sum([
        1 if args.image else 0,
        1 if args.last_frame else 0,
        len(args.ref_images or []),
    ])
    if args.mode == "text":
        return "seedance-2.0-text-to-video"
    if args.mode == "reference":
        return "seedance-2.0-reference-to-video"
    if args.mode == "image":
        return "seedance-2.0-image-to-video"
    if has_video or has_audio or n_images >= 3:
        return "seedance-2.0-reference-to-video"
    if n_images >= 1:
        return "seedance-2.0-image-to-video"
    return "seedance-2.0-text-to-video"


def _evolink_build_payload(args, model: str) -> dict:
    payload: dict = {"model": model}
    if args.prompt:
        payload["prompt"] = args.prompt
    if args.ratio:
        payload["aspect_ratio"] = args.ratio
    if args.duration:
        payload["duration"] = args.duration
    if args.resolution:
        payload["quality"] = args.resolution
    if args.seed is not None:
        payload["seed"] = args.seed
    if args.generate_audio is not None:
        payload["generate_audio"] = args.generate_audio

    image_urls: list[str] = []
    if args.image:
        image_urls.append(_image_to_data_url(args.image))
    if args.last_frame:
        image_urls.append(_image_to_data_url(args.last_frame))
    for img in (args.ref_images or []):
        image_urls.append(_image_to_data_url(img))
    if image_urls:
        payload["image_urls"] = image_urls

    if args.video:
        payload["video_urls"] = list(args.video)
    if args.audio:
        payload["audio_urls"] = list(args.audio)

    if args.web_search and "text" in model:
        payload["web_search"] = True

    return payload


def _evolink_create(args, api_key: str) -> dict:
    model = _evolink_pick_model(args)
    payload = _evolink_build_payload(args, model)
    return _http("POST", EVOLINK_CREATE, api_key, payload)


def _evolink_status(task_id: str, api_key: str) -> dict:
    return _http("GET", f"{EVOLINK_STATUS}/{task_id}", api_key)


# ---------------------------------------------------------------------------
# Unified dispatch
# ---------------------------------------------------------------------------


def _select_backend(args) -> tuple[str, str]:
    """Resolve (backend, api_key), honouring --backend override."""
    forced = getattr(args, "backend", None)
    if forced == "ark":
        key = os.environ.get("ARK_API_KEY")
        if not key:
            _die("--backend ark requires ARK_API_KEY env var")
        return "ark", key
    if forced == "evolink":
        key = os.environ.get("EVOLINK_API_KEY")
        if not key:
            _die("--backend evolink requires EVOLINK_API_KEY env var")
        return "evolink", key
    backend, key = _resolve_backend()
    if not backend:
        _die("Set ARK_API_KEY or EVOLINK_API_KEY environment variable")
    return backend, key


# ---------------------------------------------------------------------------
# Task result extraction
# ---------------------------------------------------------------------------


def _extract_task_id(resp: dict, backend: str) -> str:
    if backend == "ark":
        return resp.get("id") or resp.get("task_id", "")
    return resp.get("task_id") or resp.get("id", "")


def _extract_status(resp: dict, backend: str) -> str:
    if backend == "ark":
        return resp.get("status", "unknown")
    return resp.get("status", "unknown")


def _extract_video_url(resp: dict, backend: str) -> str | None:
    if backend == "ark":
        content = resp.get("content", {})
        if isinstance(content, dict):
            return content.get("video_url")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("video_url"):
                    return item["video_url"]
    else:
        url = resp.get("video_url")
        if url:
            return url
        results = resp.get("results") or resp.get("data", {}).get("results")
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict) and r.get("url"):
                    return r["url"]
    return None


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------


def _download(url: str, dest_dir: str, task_id: str) -> str:
    dest = Path(dest_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    ext = ".mp4"
    filepath = dest / f"{task_id}{ext}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=300) as resp:
        with open(filepath, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    return str(filepath)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_create(args) -> None:
    backend, api_key = _select_backend(args)
    if backend == "ark":
        resp = _ark_create(args, api_key)
    else:
        resp = _evolink_create(args, api_key)

    task_id = _extract_task_id(resp, backend)
    if not task_id:
        _die(f"No task_id in response: {json.dumps(resp, ensure_ascii=False)}")

    est = resp.get("estimated_time", "?")
    print(f"TASK_SUBMITTED: task_id={task_id} estimated={est}s backend={backend}")
    print(json.dumps(resp, indent=2, ensure_ascii=False))

    if getattr(args, "wait", False):
        args.task_id = task_id
        cmd_wait(args)


def cmd_status(args) -> None:
    backend, api_key = _select_backend(args)
    if backend == "ark":
        resp = _ark_status(args.task_id, api_key)
    else:
        resp = _evolink_status(args.task_id, api_key)
    status = _extract_status(resp, backend)
    video = _extract_video_url(resp, backend)
    print(f"STATUS_UPDATE: task_id={args.task_id} status={status}")
    if video:
        print(f"VIDEO_URL={video}")
    print(json.dumps(resp, indent=2, ensure_ascii=False))


def cmd_wait(args) -> None:
    backend, api_key = _select_backend(args)
    interval = getattr(args, "interval", 15) or 15
    download_dir = getattr(args, "download", None)
    t0 = time.time()

    while True:
        if backend == "ark":
            resp = _ark_status(args.task_id, api_key)
        else:
            resp = _evolink_status(args.task_id, api_key)

        status = _extract_status(resp, backend)
        elapsed = int(time.time() - t0)
        print(f"STATUS_UPDATE: task_id={args.task_id} status={status} ELAPSED={elapsed}s")

        if status in ("succeeded", "completed", "complete", "success"):
            video = _extract_video_url(resp, backend)
            if video:
                print(f"VIDEO_URL={video}")
                if download_dir:
                    path = _download(video, download_dir, args.task_id)
                    print(f"DOWNLOADED: {path}")
            else:
                print(json.dumps(resp, indent=2, ensure_ascii=False))
            return

        if status in ("failed", "error", "cancelled", "canceled"):
            print(f"Task {status}.", file=sys.stderr)
            print(json.dumps(resp, indent=2, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)

        time.sleep(interval)


def cmd_list(args) -> None:
    backend, api_key = _select_backend(args)
    if backend != "ark":
        _die("list command is only supported for the Ark backend")
    page = getattr(args, "page", 1) or 1
    size = getattr(args, "size", 10) or 10
    resp = _ark_list(api_key, page, size)
    print(json.dumps(resp, indent=2, ensure_ascii=False))


def cmd_delete(args) -> None:
    backend, api_key = _select_backend(args)
    if backend != "ark":
        _die("delete command is only supported for the Ark backend")
    resp = _ark_delete(args.task_id, api_key)
    print(f"Deleted task {args.task_id}")
    if resp:
        print(json.dumps(resp, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedance",
        description="Seedance Video Generation CLI (Volcengine Ark & EvoLink 2.0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- create ----
    c = sub.add_parser("create", help="Create a video generation task")
    c.add_argument("--prompt", "-p", help="Text prompt")
    c.add_argument("--image", "-i", help="First-frame image (URL or local path)")
    c.add_argument("--last-frame", help="Last-frame image (URL or local path)")
    c.add_argument("--ref-images", nargs="+", help="Reference images (1-4, Lite I2V)")
    c.add_argument("--video", action="append", help="Reference video URL (repeatable, 0-3)")
    c.add_argument("--audio", action="append", help="Reference audio URL (repeatable, 0-3)")
    c.add_argument("--model", "-m", choices=ALL_MODELS, help="Model ID")
    c.add_argument("--mode", choices=["text", "image", "reference"], help="Force mode")
    c.add_argument("--backend", choices=["ark", "evolink"], help="Force backend")
    c.add_argument("--ratio", choices=RATIOS, default="16:9", help="Aspect ratio (default 16:9)")
    c.add_argument("--duration", "-d", type=int, help="Duration in seconds (4-15)")
    c.add_argument("--resolution", "-r", choices=RESOLUTIONS, default="720p", help="Resolution")
    c.add_argument("--seed", type=int, help="Random seed")
    c.add_argument("--camera-fixed", action="store_true", help="Lock camera")
    c.add_argument("--watermark", action="store_true", help="Add watermark")
    c.add_argument(
        "--generate-audio", type=_str_to_bool, default=None, metavar="BOOL",
        help="Generate audio (true/false)",
    )
    c.add_argument("--draft", action="store_true", help="Draft / low-cost preview mode")
    c.add_argument("--return-last-frame", action="store_true", help="Return the last frame image")
    c.add_argument("--web-search", action="store_true", help="Enable web search (2.0 text mode)")
    c.add_argument("--wait", "-w", action="store_true", help="Wait for task completion")
    c.add_argument("--interval", type=int, default=15, help="Poll interval in seconds (default 15)")
    c.add_argument("--download", metavar="DIR", help="Download directory")

    # ---- status ----
    s = sub.add_parser("status", help="Query task status")
    s.add_argument("task_id", help="Task ID")
    s.add_argument("--backend", choices=["ark", "evolink"])

    # ---- wait ----
    w = sub.add_parser("wait", help="Wait for task to complete")
    w.add_argument("task_id", help="Task ID")
    w.add_argument("--backend", choices=["ark", "evolink"])
    w.add_argument("--interval", type=int, default=15, help="Poll interval in seconds")
    w.add_argument("--download", metavar="DIR", help="Download directory")

    # ---- list ----
    ls = sub.add_parser("list", help="List tasks (Ark only)")
    ls.add_argument("--page", type=int, default=1)
    ls.add_argument("--size", type=int, default=10)
    ls.add_argument("--backend", choices=["ark", "evolink"])

    # ---- delete ----
    d = sub.add_parser("delete", help="Cancel / delete a task (Ark only)")
    d.add_argument("task_id", help="Task ID")
    d.add_argument("--backend", choices=["ark", "evolink"])

    return parser


def _str_to_bool(v: str) -> bool:
    if v.lower() in ("true", "1", "yes", "on"):
        return True
    if v.lower() in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"Boolean expected, got '{v}'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMAND_MAP = {
    "create": cmd_create,
    "status": cmd_status,
    "wait": cmd_wait,
    "list": cmd_list,
    "delete": cmd_delete,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    COMMAND_MAP[args.command](args)


if __name__ == "__main__":
    main()
