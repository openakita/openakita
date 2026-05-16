"""End-to-end smoke test for the happyhorse-video plugin.

Drives the live OpenAkita backend over HTTP (no UI), exercising every
plugin route with the cheapest possible parameters so the user can find
the broken ones without manual click-testing every tab.

Run:
    python plugins/happyhorse-video/tests/smoke_e2e.py
    python plugins/happyhorse-video/tests/smoke_e2e.py --base http://127.0.0.1:18900
    python plugins/happyhorse-video/tests/smoke_e2e.py --skip-paid       # zero-cost subset
    python plugins/happyhorse-video/tests/smoke_e2e.py --t2v-only        # only the t2v probe

The report is printed live to stdout and also dumped to
``plugins/happyhorse-video/tests/smoke_e2e_report.json`` so the user can
correlate UI activity with the matching task ids.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PLUGIN_ID = "happyhorse-video"
HERE = Path(__file__).resolve().parent
REPORT_PATH = HERE / "smoke_e2e_report.json"


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        raw_body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, dict[str, Any] | str]:
        url = f"{self.base}/api/plugins/{PLUGIN_ID}{path}"
        if raw_body is not None:
            data = raw_body
            ct = content_type or "application/octet-stream"
        elif body is None:
            data = None
            ct = None
        else:
            data = json.dumps(body).encode("utf-8")
            ct = "application/json"
        req = request.Request(url, data=data, method=method)
        if ct:
            req.add_header("Content-Type", ct)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw.decode("utf-8"))
                except Exception:
                    return resp.status, raw.decode("utf-8", "replace")
        except error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw.decode("utf-8"))
            except Exception:
                return e.code, raw.decode("utf-8", "replace")
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}"


# ── result tracker ──────────────────────────────────────────────────


class Report:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.t0 = time.time()

    def step(self, name: str, status: str, detail: str, extra: dict[str, Any] | None = None) -> None:
        emoji = {"ok": "[OK]", "fail": "[FAIL]", "skip": "[SKIP]", "warn": "[WARN]"}.get(status, "[--]")
        print(f"{emoji} {name}: {detail}")
        self.items.append({
            "name": name,
            "status": status,
            "detail": detail,
            "at_ms": int((time.time() - self.t0) * 1000),
            **(extra or {}),
        })

    def write(self) -> None:
        summary = {
            "elapsed_ms": int((time.time() - self.t0) * 1000),
            "ok": sum(1 for i in self.items if i["status"] == "ok"),
            "fail": sum(1 for i in self.items if i["status"] == "fail"),
            "warn": sum(1 for i in self.items if i["status"] == "warn"),
            "skip": sum(1 for i in self.items if i["status"] == "skip"),
        }
        REPORT_PATH.write_text(
            json.dumps({"summary": summary, "items": self.items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n--- summary: {summary} ---")
        print(f"report: {REPORT_PATH}")


# ── helpers ─────────────────────────────────────────────────────────


def _is_ok(status: int, payload: Any) -> bool:
    if not (200 <= status < 300):
        return False
    if isinstance(payload, dict) and payload.get("ok") is False:
        return False
    return True


def _err(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("detail") or payload)[:300]
    return str(payload)[:300]


def _poll_task(c: Client, task_id: str, *, label: str, timeout_s: float = 300.0) -> tuple[bool, dict[str, Any] | str]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] | str = ""
    while time.time() < deadline:
        status, payload = c.call("GET", f"/tasks/{task_id}")
        last = payload
        if not _is_ok(status, payload):
            return False, payload
        if isinstance(payload, dict):
            task = payload.get("task") or payload
            st = (task.get("status") or "").lower()
            if st in {"succeeded", "success", "completed", "done"}:
                return True, payload
            if st in {"failed", "error", "cancelled", "canceled"}:
                return False, payload
            print(f"  ... {label} status={st}", flush=True)
        time.sleep(5)
    return False, f"timeout after {int(timeout_s)}s; last={last}"


# ── individual checks ───────────────────────────────────────────────


def check_zero_cost(c: Client, r: Report) -> None:
    print("\n=== Phase 1: zero-cost reads ===")

    s, p = c.call("GET", "/healthz")
    r.step("GET /healthz", "ok" if _is_ok(s, p) else "fail", json.dumps(p)[:200] if isinstance(p, (dict, list)) else str(p)[:200])

    s, p = c.call("GET", "/catalog")
    if _is_ok(s, p):
        modes = len(p["catalog"]["modes"]) if isinstance(p, dict) else 0
        r.step("GET /catalog", "ok", f"{modes} modes", {"sample_mode_ids": [m.get("id") for m in p["catalog"]["modes"][:3]] if isinstance(p, dict) else []})
    else:
        r.step("GET /catalog", "fail", _err(p))

    s, p = c.call("GET", "/settings")
    cfg = p.get("config") if isinstance(p, dict) else {}
    api_key_set = bool(cfg.get("api_key_set")) if isinstance(cfg, dict) else False
    r.step(
        "GET /settings",
        "ok" if _is_ok(s, p) else "fail",
        f"api_key_set={api_key_set} keys={len(cfg) if isinstance(cfg, dict) else 0}",
    )

    s, p = c.call("GET", "/python-deps/status")
    r.step("GET /python-deps/status", "ok" if _is_ok(s, p) else "fail", json.dumps(p)[:200])

    s, p = c.call("GET", "/system/components")
    r.step("GET /system/components", "ok" if _is_ok(s, p) else "fail", json.dumps(p)[:200])

    s, p = c.call("GET", "/storage/stats")
    r.step("GET /storage/stats", "ok" if _is_ok(s, p) else "fail", json.dumps(p)[:200])

    s, p = c.call("GET", "/voices")
    r.step("GET /voices", "ok" if _is_ok(s, p) else "fail", f"count={len(p.get('voices', [])) if isinstance(p, dict) else 0}")

    s, p = c.call("GET", "/figures")
    r.step("GET /figures", "ok" if _is_ok(s, p) else "fail", f"count={len(p.get('figures', [])) if isinstance(p, dict) else 0}")

    s, p = c.call("GET", "/tasks?limit=5")
    r.step("GET /tasks", "ok" if _is_ok(s, p) else "fail", f"total={p.get('total') if isinstance(p, dict) else '?'}")

    s, p = c.call("GET", "/prompt-guide")
    r.step("GET /prompt-guide", "ok" if _is_ok(s, p) else "fail", "")

    s, p = c.call("GET", "/long-video/active-chains")
    r.step("GET /long-video/active-chains", "ok" if _is_ok(s, p) else "fail", "")


def check_connectivity(c: Client, r: Report) -> None:
    print("\n=== Phase 2: connectivity probes ===")

    s, p = c.call("POST", "/test-connection", {})
    ok = isinstance(p, dict) and p.get("ok") is True
    r.step("POST /test-connection", "ok" if ok else "warn", _err(p))

    s, p = c.call("POST", "/oss/test", {})
    ok = isinstance(p, dict) and p.get("ok") is True
    r.step("POST /oss/test", "ok" if ok else "warn", _err(p))


def check_cost_preview(c: Client, r: Report) -> None:
    """Drive cost-preview across every mode using each mode's default
    model + its lowest allowed resolution + shortest allowed duration.

    Pure math on the backend — no money, no DashScope traffic. Catches
    pricing-table drift the moment a new mode or model is added.
    """
    print("\n=== Phase 3: cost preview (pure math, all modes) ===")
    status, payload = c.call("GET", "/catalog")
    if not _is_ok(status, payload) or not isinstance(payload, dict):
        r.step("cost-preview catalog fetch", "fail", _err(payload))
        return
    cat = payload.get("catalog") or {}
    modes = cat.get("modes") or []
    models = cat.get("models") or []
    defaults = cat.get("default_models") or {}

    for mode in modes:
        mode_id = mode.get("id")
        if not mode_id:
            continue
        candidates = [m for m in models if m.get("mode") == mode_id]
        chosen = None
        wanted = defaults.get(mode_id)
        if wanted:
            chosen = next((m for m in candidates if m.get("model_id") == wanted or m.get("id") == wanted), None)
        chosen = chosen or (candidates[0] if candidates else None)
        if not chosen:
            r.step(f"cost-preview {mode_id}", "warn", "no model registered")
            continue
        res_list = chosen.get("resolutions") or ["720P"]
        dur_range = chosen.get("duration_range") or [3, 15]
        body = {
            "mode": mode_id,
            "model_id": chosen.get("model_id") or chosen.get("id"),
            "duration": int(dur_range[0]) if dur_range else 3,
            "resolution": res_list[0],
            "aspect_ratio": "16:9",
            "text": "测试一下" if mode_id in {"photo_speak", "avatar_compose"} else "",
            "tts_engine": "edge" if mode_id in {"photo_speak", "avatar_compose"} else "",
        }
        s, p = c.call("POST", "/cost-preview", body)
        if _is_ok(s, p):
            r.step(
                f"cost-preview {mode_id}",
                "ok",
                f"{chosen.get('model_id')} {body['resolution']} {body['duration']}s → {p.get('formatted_total') or p.get('total')}",
                {"request": body, "response": p},
            )
        else:
            r.step(f"cost-preview {mode_id}", "fail", _err(p), {"request": body})


def check_prompt_optimize(c: Client, r: Report) -> None:
    print("\n=== Phase 4: prompt-optimize (LLM, ~free) ===")
    s, p = c.call(
        "POST",
        "/prompt-optimize",
        {"prompt": "猫追蝴蝶", "mode": "t2v", "ratio": "16:9", "duration": 3},
        timeout=90,
    )
    if _is_ok(s, p):
        out = (p.get("result") or "").strip()
        r.step("POST /prompt-optimize", "ok" if out else "warn", out[:200] or "(empty)")
    else:
        r.step("POST /prompt-optimize", "fail", _err(p))


def check_storyboard(c: Client, r: Report) -> None:
    print("\n=== Phase 5: storyboard/decompose (LLM, ~free) ===")
    s, p = c.call(
        "POST",
        "/storyboard/decompose",
        {
            "story": "一只小猫从沙发跳到地毯，再走到窗边晒太阳",
            "total_duration": 12,
            "segment_duration": 4,
            "aspect_ratio": "16:9",
            "style": "电影级画质",
        },
        timeout=120,
    )
    if _is_ok(s, p):
        segs = p.get("segments") or p.get("data", {}).get("segments") or []
        r.step("POST /storyboard/decompose", "ok" if segs else "warn", f"segments={len(segs)}")
    else:
        r.step("POST /storyboard/decompose", "fail", _err(p))


def check_edge_tts(c: Client, r: Report) -> None:
    print("\n=== Phase 6: edge-tts preview (free) ===")
    s, p = c.call(
        "POST",
        "/voices/preview",
        {"text": "你好，这是边缘 T T S 测试。", "engine": "edge", "voice_id": "zh-CN-XiaoxiaoNeural"},
        timeout=60,
    )
    if _is_ok(s, p):
        url = p.get("preview_url") or p.get("url") or ""
        path = p.get("audio_path") or ""
        if url:
            r.step("POST /voices/preview edge", "ok", f"preview_url={url}")
        elif path:
            r.step("POST /voices/preview edge", "warn", f"only got local path: {path}")
        else:
            r.step("POST /voices/preview edge", "warn", f"no audio path/url in response: {p}")
    else:
        r.step("POST /voices/preview edge", "fail", _err(p))


def check_image_cheap(c: Client, r: Report, *, do_paid: bool) -> dict[str, str] | None:
    print("\n=== Phase 7: image gen (cheapest model) ===")
    if not do_paid:
        r.step("image cheapest gen", "skip", "skipped (--skip-paid)")
        return None
    body = {
        "mode": "image_text2img",
        "prompt": "一只穿着小斗篷的橘猫坐在窗台，阳光柔和，干净极简背景",
        "size": "1024*1024",
    }
    s, p = c.call("POST", "/image-tasks", body, timeout=30)
    if not _is_ok(s, p):
        r.step("POST /image-tasks image_text2img", "fail", _err(p), {"request": body})
        return None
    task = p.get("task") or {}
    tid = task.get("task_id") or task.get("id")
    if not tid:
        r.step("POST /image-tasks image_text2img", "fail", "no task_id returned", {"response": p})
        return None
    r.step("POST /image-tasks image_text2img", "ok", f"task_id={tid}, polling…")
    ok, payload = _poll_task(c, tid, label="image", timeout_s=180)
    if not ok:
        r.step("poll image task", "fail", _err(payload), {"task_id": tid})
        return None
    task = (payload.get("task") if isinstance(payload, dict) else None) or {}
    urls = (
        task.get("image_urls")
        or (task.get("asset_paths") or {}).get("image_urls")
        or []
    )
    if not urls:
        r.step("poll image task", "warn", "succeeded but no image_urls", {"task": task})
        return None
    r.step("poll image task", "ok", f"image_url={urls[0][:120]}")
    return {"task_id": tid, "image_url": urls[0]}


def check_t2v_cheap(c: Client, r: Report, *, do_paid: bool) -> dict[str, str] | None:
    print("\n=== Phase 8: t2v shortest (happyhorse-1.0-t2v 3s 720P) ===")
    if not do_paid:
        r.step("t2v 3s 720P", "skip", "skipped (--skip-paid)")
        return None
    body = {
        "mode": "t2v",
        "model_id": "happyhorse-1.0-t2v",
        "prompt": "海上日出，慢镜头从浪花特写拉到全景，电影感",
        "duration": 3,
        "resolution": "720P",
        "aspect_ratio": "16:9",
        "client_request_id": f"smoke_{int(time.time())}_t2v",
    }
    s, p = c.call("POST", "/tasks", body, timeout=30)
    if not _is_ok(s, p):
        r.step("POST /tasks t2v 3s 720P", "fail", _err(p), {"request": body})
        return None
    task = p.get("task") or {}
    tid = task.get("task_id") or task.get("id")
    if not tid:
        r.step("POST /tasks t2v 3s 720P", "fail", "no task_id", {"response": p})
        return None
    r.step("POST /tasks t2v 3s 720P", "ok", f"task_id={tid}")
    ok, payload = _poll_task(c, tid, label="t2v", timeout_s=420)
    if not ok:
        r.step("poll t2v task", "fail", _err(payload), {"task_id": tid})
        return None
    task = (payload.get("task") if isinstance(payload, dict) else None) or {}
    url = task.get("video_url") or task.get("local_video_url") or ""
    r.step("poll t2v task", "ok" if url else "warn", f"video_url={url[:120]}")
    return {"task_id": tid, "video_url": url, "last_frame_url": task.get("last_frame_url") or ""}


def check_r2v_cheap(c: Client, r: Report, image_url: str, *, do_paid: bool) -> None:
    print("\n=== Phase 9: r2v reuse generated image ===")
    if not do_paid:
        r.step("r2v 3s 480P", "skip", "skipped (--skip-paid)")
        return
    if not image_url:
        r.step("r2v 3s 480P", "skip", "no upstream image_url")
        return
    body = {
        "mode": "r2v",
        "model_id": "wan2.6-r2v",
        "prompt": "镜头缓慢推近，光影柔和，电影质感",
        "duration": 5,
        "resolution": "480P",
        "aspect_ratio": "16:9",
        "reference_urls": [image_url],
        "client_request_id": f"smoke_{int(time.time())}_r2v",
    }
    s, p = c.call("POST", "/tasks", body, timeout=30)
    if not _is_ok(s, p):
        r.step("POST /tasks r2v", "fail", _err(p), {"request": body})
        return
    task = p.get("task") or {}
    tid = task.get("task_id") or task.get("id")
    if not tid:
        r.step("POST /tasks r2v", "fail", "no task_id", {"response": p})
        return
    r.step("POST /tasks r2v", "ok", f"task_id={tid}")
    ok, payload = _poll_task(c, tid, label="r2v", timeout_s=420)
    task = (payload.get("task") if isinstance(payload, dict) else None) or {}
    url = task.get("video_url") or ""
    r.step("poll r2v task", "ok" if (ok and url) else "fail", url[:120] if url else _err(payload), {"task_id": tid})


def check_chain_concat(c: Client, r: Report, task_ids: list[str], *, do_paid: bool) -> None:
    print("\n=== Phase 10: long-video concat ===")
    if not do_paid or len([t for t in task_ids if t]) < 2:
        r.step("POST /long-video/concat", "skip", "need >= 2 finished video tasks (paid run)")
        return
    body = {
        "task_ids": [t for t in task_ids if t],
        "transition": "cut",
        "fade_duration": 0.5,
    }
    s, p = c.call("POST", "/long-video/concat", body, timeout=300)
    r.step("POST /long-video/concat", "ok" if _is_ok(s, p) else "fail", _err(p))


# ── main ────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18900")
    ap.add_argument("--skip-paid", action="store_true", help="skip image/video generation phases")
    ap.add_argument("--t2v-only", action="store_true", help="only verify the t2v duration fix")
    ap.add_argument("--include-r2v", action="store_true", help="also run r2v + concat (extra cost)")
    args = ap.parse_args()

    c = Client(args.base)
    r = Report()

    if args.t2v_only:
        check_t2v_cheap(c, r, do_paid=True)
        r.write()
        return 0

    check_zero_cost(c, r)
    check_connectivity(c, r)
    check_cost_preview(c, r)
    check_prompt_optimize(c, r)
    check_storyboard(c, r)
    check_edge_tts(c, r)

    img = check_image_cheap(c, r, do_paid=not args.skip_paid)
    t2v = check_t2v_cheap(c, r, do_paid=not args.skip_paid)
    # r2v / concat intentionally NOT run here to keep total cost bounded;
    # enable them per-run with --include-r2v if needed.
    if args.include_r2v:
        image_url = (img or {}).get("image_url", "")
        if image_url:
            check_r2v_cheap(c, r, image_url, do_paid=not args.skip_paid)
        tids = [(t2v or {}).get("task_id") or ""]
        check_chain_concat(c, r, tids, do_paid=not args.skip_paid)

    r.write()
    return 0 if all(i["status"] != "fail" for i in r.items) else 2


if __name__ == "__main__":
    sys.exit(main())
