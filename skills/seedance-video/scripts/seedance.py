#!/usr/bin/env python3
"""Seedance Video Generation CLI — Volcengine Ark API (1.x & 2.0)."""

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

ARK_MODELS = [
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-1-5-pro-251215",
    "doubao-seedance-1-0-pro-250528",
    "doubao-seedance-1-0-pro-fast-251015",
    "doubao-seedance-1-0-lite-t2v-250428",
    "doubao-seedance-1-0-lite-i2v-250428",
]
ARK_DEFAULT_MODEL = ARK_MODELS[0]

RATIOS = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"]
RESOLUTIONS = ["480p", "720p", "1080p"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _file_to_data_url(path_or_url: str) -> str:
    """Convert a local file path to a base64 data-URL; URLs/asset URIs pass through."""
    if path_or_url.startswith(("http://", "https://", "data:", "asset://")):
        return path_or_url
    p = Path(path_or_url).expanduser().resolve()
    if not p.is_file():
        _die(f"File not found: {path_or_url}")
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
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
    return {}


# ---------------------------------------------------------------------------
# Content builder
# ---------------------------------------------------------------------------


def _build_content(args) -> list[dict]:
    """Build the Ark-style content array from CLI args."""
    content: list[dict] = []

    if args.prompt:
        content.append({"type": "text", "text": args.prompt})

    if args.image:
        role = "first_frame"
        if getattr(args, "image_role", None):
            role = args.image_role
        content.append({
            "type": "image_url",
            "image_url": {"url": _file_to_data_url(args.image)},
            "role": role,
        })

    if args.last_frame:
        content.append({
            "type": "image_url",
            "image_url": {"url": _file_to_data_url(args.last_frame)},
            "role": "last_frame",
        })

    for img in (args.ref_images or []):
        content.append({
            "type": "image_url",
            "image_url": {"url": _file_to_data_url(img)},
            "role": "reference_image",
        })

    for vid in (args.ref_videos or []):
        content.append({
            "type": "video_url",
            "video_url": {"url": vid},
            "role": "reference_video",
        })

    for aud in (args.ref_audios or []):
        content.append({
            "type": "audio_url",
            "audio_url": {"url": aud},
            "role": "reference_audio",
        })

    return content


# ---------------------------------------------------------------------------
# Ark API helpers
# ---------------------------------------------------------------------------


def _ark_create(args, api_key: str) -> dict:
    model = args.model or ARK_DEFAULT_MODEL
    payload: dict = {
        "model": model,
        "content": _build_content(args),
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
    if args.web_search:
        payload["tools"] = [{"type": "web_search"}]
    svc_tier = getattr(args, "service_tier", None)
    if svc_tier:
        payload["service_tier"] = svc_tier
        if svc_tier == "flex":
            expires = getattr(args, "expires", None) or 172800
            payload["execution_expires_after"] = expires
    cb = getattr(args, "callback_url", None)
    if cb:
        payload["callback_url"] = cb
    return _http("POST", ARK_BASE, api_key, payload)


def _ark_status(task_id: str, api_key: str) -> dict:
    return _http("GET", f"{ARK_BASE}/{task_id}", api_key)


def _ark_list(api_key: str, page: int = 1, size: int = 10) -> dict:
    url = f"{ARK_BASE}?page_num={page}&page_size={size}"
    return _http("GET", url, api_key)


def _ark_delete(task_id: str, api_key: str) -> dict:
    return _http("DELETE", f"{ARK_BASE}/{task_id}", api_key)


# ---------------------------------------------------------------------------
# Task result extraction
# ---------------------------------------------------------------------------


def _extract_task_id(resp: dict) -> str:
    return resp.get("id") or resp.get("task_id", "")


def _extract_status(resp: dict) -> str:
    return resp.get("status", "unknown")


def _extract_video_url(resp: dict) -> str | None:
    content = resp.get("content", {})
    if isinstance(content, dict):
        return content.get("video_url")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("video_url"):
                return item["video_url"]
    return None


def _extract_last_frame_url(resp: dict) -> str | None:
    content = resp.get("content", {})
    if isinstance(content, dict):
        return content.get("last_frame_url")
    return None


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------


def _download(url: str, dest_dir: str, task_id: str) -> str:
    dest = Path(dest_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    filepath = dest / f"{task_id}.mp4"
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
    api_key = _get_api_key(args)
    resp = _ark_create(args, api_key)

    task_id = _extract_task_id(resp)
    if not task_id:
        _die(f"No task_id in response: {json.dumps(resp, ensure_ascii=False)}")

    est = resp.get("estimated_time", "?")
    print(f"TASK_SUBMITTED: task_id={task_id} estimated={est}s")
    print(json.dumps(resp, indent=2, ensure_ascii=False))

    if getattr(args, "wait", False):
        args.task_id = task_id
        cmd_wait(args)


def cmd_status(args) -> None:
    api_key = _get_api_key(args)
    resp = _ark_status(args.task_id, api_key)
    status = _extract_status(resp)
    video = _extract_video_url(resp)
    print(f"STATUS_UPDATE: task_id={args.task_id} status={status}")
    if video:
        print(f"VIDEO_URL={video}")
    print(json.dumps(resp, indent=2, ensure_ascii=False))


def cmd_wait(args) -> None:
    api_key = _get_api_key(args)
    interval = getattr(args, "interval", 15) or 15
    download_dir = getattr(args, "download", None)
    t0 = time.time()

    while True:
        resp = _ark_status(args.task_id, api_key)
        status = _extract_status(resp)
        elapsed = int(time.time() - t0)
        print(f"STATUS_UPDATE: task_id={args.task_id} status={status} ELAPSED={elapsed}s")

        if status in ("succeeded", "completed", "complete", "success"):
            video = _extract_video_url(resp)
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
    api_key = _get_api_key(args)
    page = getattr(args, "page", 1) or 1
    size = getattr(args, "size", 10) or 10
    resp = _ark_list(api_key, page, size)
    print(json.dumps(resp, indent=2, ensure_ascii=False))


def cmd_delete(args) -> None:
    api_key = _get_api_key(args)
    resp = _ark_delete(args.task_id, api_key)
    print(f"Deleted task {args.task_id}")
    if resp:
        print(json.dumps(resp, indent=2, ensure_ascii=False))


def cmd_chain(args) -> None:
    """Generate multiple continuous videos by chaining last-frame → first-frame."""
    api_key = _get_api_key(args)
    prompts = args.prompts
    image_url = _file_to_data_url(args.image) if args.image else None
    interval = getattr(args, "interval", 30) or 30
    download_dir = getattr(args, "download", None)
    video_urls: list[str] = []

    for idx, prompt in enumerate(prompts):
        print(f"\n===== CHAIN [{idx + 1}/{len(prompts)}] =====")
        content: list[dict] = [{"type": "text", "text": prompt}]
        if image_url:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url},
            })

        payload: dict = {
            "model": args.model or ARK_DEFAULT_MODEL,
            "content": content,
            "return_last_frame": True,
        }
        if args.ratio:
            payload["ratio"] = args.ratio
        if args.duration:
            payload["duration"] = args.duration
        if args.resolution:
            payload["resolution"] = args.resolution
        gen_audio = getattr(args, "generate_audio", None)
        if gen_audio is not None:
            payload["generate_audio"] = gen_audio

        resp = _http("POST", ARK_BASE, api_key, payload)
        task_id = _extract_task_id(resp)
        if not task_id:
            _die(f"No task_id in response: {json.dumps(resp, ensure_ascii=False)}")
        print(f"TASK_SUBMITTED: task_id={task_id}")

        t0 = time.time()
        while True:
            resp = _ark_status(task_id, api_key)
            status = _extract_status(resp)
            elapsed = int(time.time() - t0)
            print(f"STATUS_UPDATE: task_id={task_id} status={status} ELAPSED={elapsed}s")

            if status in ("succeeded", "completed", "complete", "success"):
                video = _extract_video_url(resp)
                last_frame = _extract_last_frame_url(resp)
                if video:
                    print(f"VIDEO_URL={video}")
                    video_urls.append(video)
                    if download_dir:
                        path = _download(video, download_dir, task_id)
                        print(f"DOWNLOADED: {path}")
                image_url = last_frame
                break

            if status in ("failed", "error", "cancelled", "canceled"):
                _die(f"Chain step {idx + 1} failed: {json.dumps(resp, ensure_ascii=False)}")

            time.sleep(interval)

    print(f"\n===== CHAIN COMPLETE: {len(video_urls)} videos =====")
    for i, url in enumerate(video_urls):
        print(f"VIDEO_{i + 1}={url}")


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


def _get_api_key(args) -> str:
    key = os.environ.get("ARK_API_KEY")
    if not key:
        _die("Set ARK_API_KEY environment variable")
    return key


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedance",
        description="Seedance Video Generation CLI (Volcengine Ark API)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- create ----
    c = sub.add_parser("create", help="Create a video generation task")
    c.add_argument("--prompt", "-p", help="Text prompt")
    c.add_argument("--image", "-i", help="First-frame / reference image (URL or local path)")
    c.add_argument(
        "--image-role", choices=["first_frame", "reference_image"],
        default="first_frame",
        help="Role for --image (default: first_frame)",
    )
    c.add_argument("--last-frame", help="Last-frame image (URL or local path)")
    c.add_argument(
        "--ref-images", nargs="+",
        help="Reference images with role=reference_image (1-9, Seedance 2.0)",
    )
    c.add_argument(
        "--ref-videos", nargs="+",
        help="Reference video URLs (0-3, Seedance 2.0)",
    )
    c.add_argument(
        "--ref-audios", nargs="+",
        help="Reference audio URLs (0-3, Seedance 2.0)",
    )
    c.add_argument("--model", "-m", choices=ARK_MODELS, help="Model ID")
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
    c.add_argument(
        "--service-tier", choices=["default", "flex"],
        help="Service tier: default (online) or flex (offline, 50%% cost)",
    )
    c.add_argument("--expires", type=int, help="Offline task timeout in seconds (default 172800)")
    c.add_argument("--callback-url", help="Webhook URL for task status notifications")
    c.add_argument("--wait", "-w", action="store_true", help="Wait for task completion")
    c.add_argument(
        "--interval", type=int, default=15, help="Poll interval in seconds (default 15)"
    )
    c.add_argument("--download", metavar="DIR", help="Download directory")

    # ---- status ----
    s = sub.add_parser("status", help="Query task status")
    s.add_argument("task_id", help="Task ID")

    # ---- wait ----
    w = sub.add_parser("wait", help="Wait for task to complete")
    w.add_argument("task_id", help="Task ID")
    w.add_argument("--interval", type=int, default=15, help="Poll interval in seconds")
    w.add_argument("--download", metavar="DIR", help="Download directory")

    # ---- list ----
    ls = sub.add_parser("list", help="List tasks")
    ls.add_argument("--page", type=int, default=1)
    ls.add_argument("--size", type=int, default=10)

    # ---- delete ----
    d = sub.add_parser("delete", help="Cancel / delete a task")
    d.add_argument("task_id", help="Task ID")

    # ---- chain ----
    ch = sub.add_parser("chain", help="Generate continuous videos (last-frame chaining)")
    ch.add_argument("prompts", nargs="+", help="Prompts for each video segment")
    ch.add_argument("--image", "-i", help="Initial first-frame image (URL or local path)")
    ch.add_argument("--model", "-m", choices=ARK_MODELS, help="Model ID")
    ch.add_argument("--ratio", choices=RATIOS, default="adaptive", help="Aspect ratio")
    ch.add_argument("--duration", "-d", type=int, default=5, help="Duration per segment")
    ch.add_argument("--resolution", "-r", choices=RESOLUTIONS, default="720p")
    ch.add_argument(
        "--generate-audio", type=_str_to_bool, default=None, metavar="BOOL",
    )
    ch.add_argument("--interval", type=int, default=30, help="Poll interval (default 30)")
    ch.add_argument("--download", metavar="DIR", help="Download directory")

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
    "chain": cmd_chain,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    COMMAND_MAP[args.command](args)


if __name__ == "__main__":
    main()

