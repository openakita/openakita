"""image-edit — beginner-friendly inpaint / mask edit.

Flow:
  upload image → draw mask in browser → 一句话描述要改的内容 →
  intent verify → cost preview → run via providers.select_provider() → 庆祝
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    CostEstimator,
    ErrorCoach,
    IntentVerifier,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
    VendorError,
    add_upload_preview_route,
    build_preview_url,
    collect_storage_stats,
)

from providers import select_provider
from task_manager import ImageEditTaskManager

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    source_path: str
    mask_path: str | None = None
    prompt: str = Field(..., min_length=1)
    negative_prompt: str = ""
    size: str = "1024x1024"
    n: int = 1
    provider: str = "auto"
    intent_hint: str = ""


class IntentBody(BaseModel):
    hint: str = ""
    has_mask: bool = False


class CostBody(BaseModel):
    provider: str = "auto"
    n: int = 1
    size: str = "1024x1024"


# Per-provider price table (vendor-native units).  Update as providers tweak.
_PRICE_TABLE: dict[str, dict[str, Any]] = {
    "openai-gpt-image-1": {"unit": "image", "currency": "USD",
                           "by_size": {"1024x1024": 0.04, "1024x1536": 0.06,
                                       "1536x1024": 0.06}},
    "dashscope-wanx-edit": {"unit": "image", "currency": "CNY",
                             "by_size": {"1024x1024": 0.20, "1280x1280": 0.30}},
    "stub-local":          {"unit": "image", "currency": "CNY", "by_size": {"_": 0.0}},
}


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = ImageEditTaskManager(data_dir / "image_edit.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._verifier: IntentVerifier | None = None
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "image_edit_create",
                 "description": "Edit an image with a text prompt and optional mask.",
                 "input_schema": {"type": "object",
                                  "properties": {"source_path": {"type": "string"},
                                                 "prompt": {"type": "string"},
                                                 "mask_path": {"type": "string"}},
                                  "required": ["source_path", "prompt"]}},
                {"name": "image_edit_status",
                 "description": "Get the status of an image edit task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "image_edit_list",
                 "description": "List recent image edit tasks.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "image_edit_cancel",
                 "description": "Cancel a running image edit task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
            ],
            self._handle_tool_call,
        )
        api.log("image-edit loaded")

    async def on_unload(self) -> None:
        workers = [t for t in list(self._workers.values()) if not t.done()]
        for t in workers:
            t.cancel()
        if workers:
            results = await asyncio.gather(*workers, return_exceptions=True)
            for res in results:
                if isinstance(res, asyncio.CancelledError):
                    continue
                if isinstance(res, Exception):
                    self._api.log(
                        f"image-edit on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "image_edit_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建任务 {tid}"
            if tool_name == "image_edit_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "image_edit_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "image_edit_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    def _register_routes(self, router: APIRouter) -> None:
        # Issue #479: serve previously uploaded images so the UI can render
        # <img src="/api/plugins/image-edit/uploads/<file>"> after upload.
        add_upload_preview_route(
            router,
            base_dir=self._api.get_data_dir() / "uploads",
        )

        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "image-edit"}

        @router.get("/config")
        async def get_config():
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.get("/providers")
        async def providers():
            try:
                p = select_provider("auto")
                return {"available": [p.name], "active": p.name}
            except VendorError as e:
                return {"available": [], "active": None, "error": str(e)}

        @router.post("/intent")
        async def intent(body: IntentBody):
            v = self._get_verifier()
            ctx = "用户已上传遮罩" if body.has_mask else "用户没有遮罩(全图编辑)"
            if not v:
                return {"summary": body.hint or "(未配置 LLM)",
                        "confidence": "low", "clarifying_questions": [],
                        "risks": ["未配置 LLM 大脑，跳过意图复核"]}
            res = await v.verify(body.hint or "我想改一下这张图",
                                 attachments_summary=ctx)
            return res.to_dict()

        @router.post("/cost")
        async def cost(body: CostBody):
            try:
                p = select_provider(body.provider)
            except VendorError as e:
                # If user explicitly asked for a provider that isn't ready
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            tariff = _PRICE_TABLE.get(p.name, _PRICE_TABLE["stub-local"])
            unit_price = tariff["by_size"].get(body.size, next(iter(tariff["by_size"].values())))
            est = CostEstimator(currency=tariff["currency"])
            est.add(f"{p.name} {body.size}",
                    units=max(1, body.n), unit_label=tariff["unit"],
                    unit_price=float(unit_price))
            est.note(f"使用 provider: {p.name}")
            return est.build(confidence="high",
                             sample_label=f"{p.name} {body.size}").to_dict()

        @router.post("/upload")
        async def upload(file: UploadFile = File(...), kind: str = Form("image")):
            data_dir = self._api.get_data_dir() / "uploads" / kind
            data_dir.mkdir(parents=True, exist_ok=True)
            target = data_dir / file.filename
            with target.open("wb") as fp:
                while chunk := await file.read(1024 * 1024):
                    fp.write(chunk)
            rel = target.relative_to(self._api.get_data_dir() / "uploads")
            return {
                "path": str(target),
                "size": target.stat().st_size,
                "url": build_preview_url("image-edit", rel),
            }

        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["source_path", "prompt"],
                non_empty_strings=["source_path", "prompt"],
            )
            if gate.blocking:
                rendered = self._coach.render(ValueError(gate.message), raw_message=gate.message)
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            try:
                tid = await self._create(body)
            except FileNotFoundError as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=404, detail=rendered.to_dict())
            return {"task_id": tid, "status": "queued"}

        @router.get("/tasks")
        async def list_tasks(status: str | None = None, limit: int = 50):
            rows = await self._tm.list_tasks(status=status, limit=limit)
            return [r.to_dict() for r in rows]

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None:
                rendered = self._coach.render(status=404, raw_message=f"task {task_id} not found")
                raise HTTPException(status_code=404, detail=rendered.to_dict())
            return rec.to_dict()

        @router.post("/tasks/{task_id}/cancel")
        async def cancel(task_id: str):
            out = await self._cancel(task_id)
            if not out:
                raise HTTPException(status_code=404, detail={"problem": "task not found"})
            return {"ok": True, "status": out.status}

        @router.get("/storage-stats")
        async def storage_stats():
            data_dir = self._api.get_data_dir()
            stats = await collect_storage_stats(
                [data_dir / "uploads", data_dir / "outputs"], max_files=2000,
            )
            return stats.to_dict()

    # ── core ──

    def _get_verifier(self) -> IntentVerifier | None:
        if self._verifier is not None:
            return self._verifier
        try:
            brain = self._api.get_brain()
        except Exception:  # noqa: BLE001
            brain = None
        if not brain:
            return None
        async def llm_call(messages, max_tokens: int = 500, **_kw):
            sys = "\n".join(m["content"] for m in messages if m.get("role") == "system")
            usr = "\n".join(m["content"] for m in messages if m.get("role") == "user")
            think = getattr(brain, "think_lightweight", None)
            if not callable(think):
                return ""
            resp = await think(prompt=usr, system=sys or None, max_tokens=max_tokens)
            text = getattr(resp, "text", None) or getattr(resp, "content", None) or ""
            if not isinstance(text, str):
                try: text = "".join(getattr(b, "text", "") for b in text)
                except TypeError: text = str(text)
            return text
        self._verifier = IntentVerifier(
            llm_call=llm_call,
            plugin_specific_context=(
                "当前插件: image-edit；用途: 用一句话改图（局部 mask 重绘 / 全图风格化）。"
                "支持的供应商: gpt-image-1（最稳）、通义万相（备用）。"
            ),
        )
        return self._verifier

    async def _create(self, body: CreateBody) -> str:
        source = Path(body.source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"源图片不存在: {source}")
        mask_path = None
        if body.mask_path:
            mp = Path(body.mask_path).expanduser().resolve()
            if mp.exists() and mp.is_file():
                mask_path = str(mp)

        tid = await self._tm.create_task(
            prompt=body.prompt,
            params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={"source_image_path": str(source),
                   "mask_image_path": mask_path or "",
                   "provider": body.provider},
        )
        worker = asyncio.create_task(self._run(tid))
        self._workers[tid] = worker
        worker.add_done_callback(lambda _t, k=tid: self._workers.pop(k, None))
        return tid

    async def _cancel(self, task_id: str):
        worker = self._workers.pop(task_id, None)
        if worker and not worker.done():
            worker.cancel()
        return await self._tm.cancel_task(task_id)

    async def _run(self, task_id: str) -> None:
        rec = await self._tm.get_task(task_id)
        if rec is None: return
        params = rec.params
        source = Path(rec.extra.get("source_image_path") or "")
        mask = Path(rec.extra.get("mask_image_path") or "") if rec.extra.get("mask_image_path") else None
        if not source.exists():
            await self._fail(task_id, FileNotFoundError(f"source missing: {source}"))
            return

        try:
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "submit"})
            output_dir = self._api.get_data_dir() / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)

            prov = select_provider(params.get("provider", "auto"))
            await self._tm.update_task(task_id, extra={"provider": prov.name})

            result = await prov.edit(
                image_path=source,
                mask_path=mask if mask and mask.exists() else None,
                prompt=params.get("prompt", ""),
                negative_prompt=params.get("negative_prompt", ""),
                size=params.get("size", "1024x1024"),
                n=int(params.get("n", 1)),
                output_dir=output_dir,
            )

            paths = [str(p) for p in result.output_paths]
            import json
            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"provider": result.provider, "output_paths": paths},
                extra={"output_paths_json": json.dumps(paths, ensure_ascii=False)},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "output_paths": paths,
                                               "provider": result.provider})
        except asyncio.CancelledError:
            await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
            raise
        except Exception as e:  # noqa: BLE001
            await self._fail(task_id, e)

    async def _fail(self, task_id: str, exc: Exception) -> None:
        rendered = self._coach.render(exc)
        await self._tm.update_task(
            task_id, status=TaskStatus.FAILED.value,
            error_message=rendered.problem, result={"error": rendered.to_dict()},
        )
        self._events.emit("task_updated", {"id": task_id, "status": "failed",
                                           "error": rendered.to_dict()})
