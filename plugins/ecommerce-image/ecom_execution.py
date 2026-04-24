"""Execution strategies and step handlers for the e-commerce content plugin.

Four strategies (locked — do NOT add more; use escape-hatch custom routes instead):
  - PromptTemplateStrategy: template fill -> single API call
  - AgentStrategy: LLM reasoning -> optimized prompt -> API call
  - PipelineStrategy: multi-step sequential (with StepHandler registry)
  - BatchStrategy: N variations of another strategy in parallel
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypedDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed configs (not a catch-all dict)
# ---------------------------------------------------------------------------

class PromptTemplateConfig(TypedDict, total=False):
    template_mode: str  # "format" (default) | "safe_substitute"


class AgentConfig(TypedDict, total=False):
    agent_system_prompt: str
    optimize_level: str  # light | professional | creative
    fallback_to_template: bool


class PipelineConfig(TypedDict, total=False):
    steps: list[dict]
    on_step_error: str  # "abort" (default) | "skip"


class BatchConfig(TypedDict, total=False):
    base_strategy: str  # prompt_template | agent
    variation_source: str
    max_concurrent: int
    batch_prompt_mode: str  # individual (default) | bulk


# ---------------------------------------------------------------------------
# ExecutionContext
# ---------------------------------------------------------------------------

@dataclass
class ExecutionContext:
    """All dependencies available to strategies at runtime."""

    dashscope: Any  # EcomClient
    ark: Any  # EcomVideoClient | None
    task_manager: Any  # TaskManager
    brain: Any  # OpenAkita Brain
    plugin_api: Any  # PluginAPI
    feature: Any  # FeatureDefinition
    semaphore: asyncio.Semaphore | None = None
    defaults: dict | None = None  # global defaults (default_image_model, default_video_model, default_image_size, watermark)


def resolve_model(params: dict, ctx: ExecutionContext) -> str:
    """Pick model: user input -> feature default -> global default by provider."""
    v = params.get("model")
    if v:
        return str(v)
    if getattr(ctx.feature, "default_model", None):
        return ctx.feature.default_model
    d = ctx.defaults or {}
    if ctx.feature.api_provider == "ark":
        return d.get("default_video_model") or ""
    return d.get("default_image_model") or ""


RATIO_SIZE_MAP: dict[str, dict[str, str]] = {
    "1K": {
        "1:1": "1024*1024", "16:9": "1344*768", "9:16": "768*1344",
        "4:3": "1184*864", "3:4": "864*1184",
    },
    "2K": {
        "1:1": "2048*2048", "16:9": "2688*1536", "9:16": "1536*2688",
        "4:3": "2368*1728", "3:4": "1728*2368",
    },
    "4K": {
        "1:1": "4096*4096", "16:9": "4096*2304", "9:16": "2304*4096",
        "4:3": "4096*3072", "3:4": "3072*4096",
    },
}


def resolve_size(params: dict, ctx: ExecutionContext) -> str:
    """Combine ratio + resolution into a final size string for DashScope."""
    resolution = params.get("size") or ""
    ratio = params.get("ratio") or "auto"

    if not resolution:
        d = ctx.defaults or {}
        resolution = d.get("default_image_size") or "2K"

    if ratio == "auto" or not ratio:
        return resolution

    if resolution in RATIO_SIZE_MAP and ratio in RATIO_SIZE_MAP[resolution]:
        return RATIO_SIZE_MAP[resolution][ratio]

    return resolution


def resolve_watermark(params: dict, ctx: ExecutionContext) -> bool:
    """Pick watermark flag: user param -> global default."""
    if "watermark" in params and params["watermark"] is not None and params["watermark"] != "":
        v = params["watermark"]
    else:
        v = (ctx.defaults or {}).get("watermark")
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _to_bool(v: Any, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _to_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _build_video_kwargs(params: dict, ctx: ExecutionContext) -> dict:
    """Pick all Ark video params from a single user/feature param dict.

    Centralizes the ratio / duration / resolution / generate_audio / seed /
    camera_fixed / draft / return_last_frame / web_search passthrough so all
    three strategies (PromptTemplate, Agent, GenerateVideoStep) stay in sync.
    """
    defaults = ctx.defaults or {}
    return {
        "ratio": str(params.get("ratio") or defaults.get("default_ratio") or "16:9"),
        "duration": _to_int(params.get("duration"), 5),
        "resolution": str(params.get("resolution") or defaults.get("default_resolution") or "720p"),
        "n": _to_int(params.get("quantity") or params.get("n"), 1),
        "generate_audio": _to_bool(params.get("generate_audio"), True),
        "seed": _to_int(params.get("seed"), -1),
        "watermark": resolve_watermark(params, ctx),
        "camera_fixed": _to_bool(params.get("camera_fixed"), False),
        "draft": _to_bool(params.get("draft"), False),
        "return_last_frame": _to_bool(params.get("return_last_frame"), False),
        "web_search": _to_bool(params.get("web_search"), False),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def safe_format(template: str, params: dict) -> str:
    """Fill template with params; missing keys become empty strings."""
    return string.Formatter().vformat(template, (), _SafeDict(params))


def split_params(feature: Any, params: dict) -> tuple[dict, dict]:
    """Split user params into text_params (for prompt) and asset_params (for API content)."""
    text_params: dict[str, Any] = {}
    asset_params: dict[str, Any] = {}
    image_types = {"image_upload", "multi_image"}
    for p in feature.params:
        if p.type in image_types:
            if params.get(p.id):
                asset_params[p.id] = params[p.id]
        else:
            text_params[p.id] = params.get(p.id, p.default or "")
    return text_params, asset_params


async def resolve_assets(asset_params: dict, task_manager: Any) -> dict:
    """Resolve asset_id values to file paths / base64 for API calls.

    Supports single asset_id (str) and multi-image lists (list[str]).
    For lists, the value becomes a list of dicts under the same key.
    """
    resolved: dict[str, Any] = {}
    for key, value in asset_params.items():
        if not value:
            continue
        if isinstance(value, list):
            items = []
            for aid in value:
                if not aid:
                    continue
                asset = await task_manager.get_asset(aid)
                if asset and Path(asset["file_path"]).is_file():
                    data = Path(asset["file_path"]).read_bytes()
                    b64 = base64.b64encode(data).decode("ascii")
                    items.append({"file_path": asset["file_path"], "base64": b64})
            if items:
                resolved[key] = items
        else:
            asset = await task_manager.get_asset(value)
            if asset and Path(asset["file_path"]).is_file():
                data = Path(asset["file_path"]).read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                resolved[key] = {"file_path": asset["file_path"], "base64": b64}
    return resolved


async def _persist_api_result(
    ctx: "ExecutionContext",
    api_result: dict,
    *,
    prompt: str,
    model: str,
    execution_mode: str,
    params: dict,
    revised_prompt: str | None = None,
) -> dict:
    """Persist an API result.

    Async results (wan2.x via EP_IMAGE_GEN): have task_id, status=running, polled later.
    Sync results (qwen via EP_MULTIMODAL): have image_urls immediately, status=completed.
    """
    task_type = "video" if ctx.feature.api_provider == "ark" else "image"
    api_task_id = api_result.get("task_id", "") or ""
    image_urls = api_result.get("image_urls", []) or []

    is_sync_done = not api_task_id and image_urls
    status = "succeeded" if is_sync_done else "running"

    extra: dict[str, Any] = {}
    if revised_prompt is not None:
        extra["revised_prompt"] = revised_prompt

    task = await ctx.task_manager.create_task(
        feature_id=ctx.feature.id,
        module=ctx.feature.module,
        task_type=task_type,
        api_provider=ctx.feature.api_provider,
        api_task_id=api_task_id,
        status=status,
        prompt=prompt,
        model=model,
        execution_mode=execution_mode,
        params=params,
        **extra,
    )

    if is_sync_done:
        import json as _json
        await ctx.task_manager.update_task(
            task["id"],
            image_urls=_json.dumps(image_urls),
            status="succeeded",
        )
        task["image_urls"] = image_urls
        task["status"] = "succeeded"

    return task


def _extract_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("content", "")
    return getattr(result, "content", "") or str(result)


# ---------------------------------------------------------------------------
# Strategy base
# ---------------------------------------------------------------------------

class ExecutionStrategy(ABC):
    """Abstract base for all execution strategies."""

    @abstractmethod
    async def execute(self, params: dict, ctx: ExecutionContext) -> dict:
        """Run the feature, return task dict."""
        ...

    async def validate(self, params: dict, ctx: ExecutionContext) -> list[str]:
        errors: list[str] = []
        for p in ctx.feature.params:
            if p.required and not params.get(p.id):
                errors.append(f"缺少必填参数: {p.label}")
        return errors


# ---------------------------------------------------------------------------
# PromptTemplateStrategy
# ---------------------------------------------------------------------------

class PromptTemplateStrategy(ExecutionStrategy):
    """Template fill -> single API call."""

    async def execute(self, params: dict, ctx: ExecutionContext) -> dict:
        text_params, asset_params = split_params(ctx.feature, params)
        prompt = safe_format(ctx.feature.prompt_template or "", text_params)
        resolved = await resolve_assets(asset_params, ctx.task_manager)

        chosen_model = resolve_model(params, ctx)
        sem = ctx.semaphore or asyncio.Semaphore(10)
        async with sem:
            if ctx.feature.api_provider == "dashscope":
                api_result = await ctx.dashscope.generate(
                    model=chosen_model,
                    prompt=prompt,
                    images=resolved,
                    capability=ctx.feature.api_capability,
                    size=resolve_size(params, ctx),
                    n=int(params.get("quantity", 1)),
                )
            else:
                api_result = await ctx.ark.create_task(
                    model=chosen_model,
                    prompt=prompt,
                    images=resolved,
                    **_build_video_kwargs(params, ctx),
                )

        task = await _persist_api_result(
            ctx, api_result, prompt=prompt, model=chosen_model,
            execution_mode="prompt_template", params=params,
        )
        return task


# ---------------------------------------------------------------------------
# AgentStrategy
# ---------------------------------------------------------------------------

class AgentStrategy(ExecutionStrategy):
    """Direct prompt -> API call.

    The Brain-powered prompt optimization has moved to the ``/prompt-optimize``
    endpoint invoked by the frontend "AI 优化" button *before* submit.
    At submit time we use the prompt text as-is; if empty we fall back to
    ``safe_format(prompt_template, text_params)``.

    For suite/multi-image features (batch_capable=True with suite_count or
    detail_count), the strategy generates multiple images:
      1. If the prompt is JSON ``{"prompts": [...]}`` (from AI optimizer), parse
         and generate one image per prompt entry.
      2. Otherwise, use the prompt_template with ``scene_index`` to generate
         N images, where N comes from suite_count / detail_count.
    """

    _COUNT_KEYS = ("suite_count", "detail_count")

    async def execute(self, params: dict, ctx: ExecutionContext) -> dict:
        text_params, asset_params = split_params(ctx.feature, params)

        prompt = (text_params.get("prompt") or "").strip()
        if not prompt:
            cfg = ctx.feature.execution_config or {}
            if cfg.get("fallback_to_template") and ctx.feature.prompt_template:
                prompt = safe_format(ctx.feature.prompt_template, text_params)
            elif ctx.feature.prompt_template:
                prompt = safe_format(ctx.feature.prompt_template, text_params)
        if not prompt:
            raise RuntimeError("提示词为空，请先填写或点击「AI 优化」生成提示词")

        resolved = await resolve_assets(asset_params, ctx.task_manager)

        count = self._get_count(params)
        prompts_list = self._try_parse_multi_prompts(prompt)

        if prompts_list and len(prompts_list) > 1:
            return await self._execute_multi(
                prompts_list, resolved, params, text_params, ctx,
            )
        if count > 1 and ctx.feature.prompt_template:
            if prompts_list and len(prompts_list) == 1:
                text_params = {**text_params, "prompt": prompts_list[0]}
            return await self._execute_suite(
                count, text_params, resolved, params, ctx,
            )

        chosen_model = resolve_model(params, ctx)
        sem = ctx.semaphore or asyncio.Semaphore(10)
        async with sem:
            if ctx.feature.api_provider == "dashscope":
                api_result = await ctx.dashscope.generate(
                    model=chosen_model,
                    prompt=prompt,
                    images=resolved,
                    capability=ctx.feature.api_capability,
                    size=resolve_size(params, ctx),
                    n=int(params.get("quantity", 1)),
                )
            else:
                api_result = await ctx.ark.create_task(
                    model=chosen_model,
                    prompt=prompt,
                    images=resolved,
                    **_build_video_kwargs(params, ctx),
                )

        task = await _persist_api_result(
            ctx, api_result, prompt=prompt, model=chosen_model,
            execution_mode="agent", params=params,
        )
        return task

    # -- multi-image helpers ------------------------------------------------

    def _get_count(self, params: dict) -> int:
        for key in self._COUNT_KEYS:
            v = params.get(key)
            if v is not None:
                try:
                    return max(1, int(v))
                except (ValueError, TypeError):
                    pass
        return 1

    @staticmethod
    def _try_parse_multi_prompts(prompt: str) -> list[str] | None:
        """Try to extract a list of prompt strings from JSON output."""
        import json as _json
        import re as _re

        text = prompt.strip()
        if not text:
            return None

        fence = _re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()

        if not (text.startswith("{") or text.startswith("[")):
            first_brace = text.find("{")
            first_bracket = text.find("[")
            idx = -1
            if first_brace >= 0 and first_bracket >= 0:
                idx = min(first_brace, first_bracket)
            elif first_brace >= 0:
                idx = first_brace
            elif first_bracket >= 0:
                idx = first_bracket
            if idx >= 0:
                text = text[idx:]

        if text.startswith("{"):
            last = text.rfind("}")
            if last > 0:
                text = text[: last + 1]
        elif text.startswith("["):
            last = text.rfind("]")
            if last > 0:
                text = text[: last + 1]

        try:
            data = _json.loads(text)
        except _json.JSONDecodeError:
            return None
        if isinstance(data, list):
            return [str(p) for p in data if p]
        if isinstance(data, dict) and "prompts" in data:
            entries = data["prompts"]
            if isinstance(entries, list):
                out: list[str] = []
                for e in entries:
                    if isinstance(e, str):
                        out.append(e)
                    elif isinstance(e, dict) and "prompt" in e:
                        out.append(str(e["prompt"]))
                return out if out else None
        return None

    async def _execute_multi(
        self,
        prompts: list[str],
        resolved: dict,
        params: dict,
        text_params: dict,
        ctx: "ExecutionContext",
    ) -> dict:
        """Generate one image per prompt entry from parsed JSON, using parent-child tasks."""
        chosen_model = resolve_model(params, ctx)
        size = resolve_size(params, ctx)
        total = len(prompts)

        parent = await ctx.task_manager.create_task(
            feature_id=ctx.feature.id,
            module=ctx.feature.module,
            task_type="image",
            api_provider=ctx.feature.api_provider,
            status="running",
            prompt=f"套图任务（{total}张）",
            model=chosen_model,
            execution_mode="agent",
            params=params,
            progress_current=0,
            progress_total=total,
        )

        sem = ctx.semaphore or asyncio.Semaphore(10)
        for i, p in enumerate(prompts):
            try:
                async with sem:
                    if ctx.feature.api_provider == "dashscope":
                        api_result = await ctx.dashscope.generate(
                            model=chosen_model,
                            prompt=p,
                            images=resolved,
                            capability=ctx.feature.api_capability,
                            size=size,
                            n=1,
                        )
                    else:
                        api_result = await ctx.ark.create_task(
                            model=chosen_model,
                            prompt=p,
                            images=resolved,
                            **_build_video_kwargs(params, ctx),
                        )
                child = await _persist_api_result(
                    ctx, api_result, prompt=p, model=chosen_model,
                    execution_mode="agent", params=params,
                )
                await ctx.task_manager.update_task(
                    child["id"], batch_parent_id=parent["id"],
                )
                if child.get("status") == "succeeded":
                    try:
                        ctx.plugin_api.broadcast_ui_event(
                            "task_update", {"task_id": child["id"], "status": "succeeded"},
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Suite child %d/%d failed: %s", i + 1, total, e)

            current = await ctx.task_manager.increment_progress(parent["id"])
            try:
                ctx.plugin_api.broadcast_ui_event(
                    "task_update",
                    {"task_id": parent["id"], "progress": current, "total": total},
                )
            except Exception:
                pass

        await ctx.task_manager.recompute_batch_parent_status(parent["id"])
        parent = await ctx.task_manager.get_task(parent["id"]) or parent
        return parent

    async def _execute_suite(
        self,
        count: int,
        text_params: dict,
        resolved: dict,
        params: dict,
        ctx: "ExecutionContext",
    ) -> dict:
        """Generate N images using prompt_template with scene_index 1..N, using parent-child tasks."""
        chosen_model = resolve_model(params, ctx)
        size = resolve_size(params, ctx)

        parent = await ctx.task_manager.create_task(
            feature_id=ctx.feature.id,
            module=ctx.feature.module,
            task_type="image",
            api_provider=ctx.feature.api_provider,
            status="running",
            prompt=f"套图任务（{count}张）- {text_params.get('product_name', '')}",
            model=chosen_model,
            execution_mode="agent",
            params=params,
            progress_current=0,
            progress_total=count,
        )

        sem = ctx.semaphore or asyncio.Semaphore(10)
        for idx in range(1, count + 1):
            tp = {**text_params, "scene_index": str(idx)}
            prompt = safe_format(ctx.feature.prompt_template or "", tp)
            try:
                async with sem:
                    if ctx.feature.api_provider == "dashscope":
                        api_result = await ctx.dashscope.generate(
                            model=chosen_model,
                            prompt=prompt,
                            images=resolved,
                            capability=ctx.feature.api_capability,
                            size=size,
                            n=1,
                        )
                    else:
                        api_result = await ctx.ark.create_task(
                            model=chosen_model,
                            prompt=prompt,
                            images=resolved,
                            **_build_video_kwargs(params, ctx),
                        )
                child = await _persist_api_result(
                    ctx, api_result, prompt=prompt, model=chosen_model,
                    execution_mode="agent", params=params,
                )
                await ctx.task_manager.update_task(
                    child["id"], batch_parent_id=parent["id"],
                )
                if child.get("status") == "succeeded":
                    try:
                        ctx.plugin_api.broadcast_ui_event(
                            "task_update", {"task_id": child["id"], "status": "succeeded"},
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Suite child %d/%d failed: %s", idx, count, e)

            current = await ctx.task_manager.increment_progress(parent["id"])
            try:
                ctx.plugin_api.broadcast_ui_event(
                    "task_update",
                    {"task_id": parent["id"], "progress": current, "total": count},
                )
            except Exception:
                pass

        await ctx.task_manager.recompute_batch_parent_status(parent["id"])
        parent = await ctx.task_manager.get_task(parent["id"]) or parent
        return parent

    @staticmethod
    def _build_user_msg(
        text_params: dict, feature: Any, *, is_video: bool = False, params: dict | None = None,
    ) -> str:
        parts: list[str] = []
        if text_params.get("product_name"):
            parts.append(f"产品名称: {text_params['product_name']}")
        if text_params.get("selling_points"):
            parts.append(f"产品卖点: {text_params['selling_points']}")
        if text_params.get("prompt"):
            parts.append(f"用户描述: {text_params['prompt']}")
        if text_params.get("reference_desc"):
            parts.append(f"参考描述: {text_params['reference_desc']}")
        if text_params.get("target_character"):
            parts.append(f"目标角色: {text_params['target_character']}")
        if text_params.get("storyboard_script"):
            parts.append(f"故事脚本: {text_params['storyboard_script']}")
        remaining = {
            k: v for k, v in text_params.items()
            if k not in (
                "product_name", "selling_points", "prompt", "reference_desc",
                "target_character", "storyboard_script",
            )
            and v
        }
        if remaining:
            parts.append(f"其他参数: {remaining}")

        if is_video:
            p = params or {}
            ratio = p.get("ratio") or "16:9"
            duration = p.get("duration") or 5
            mode = "i2v" if any(k in text_params for k in ("ref_image",)) else "t2v"
            parts.append(f"\n## 视频参数\n模式: {mode}, 时长: {duration}秒, 比例: {ratio}")
            return "\n".join(parts) or "请生成一段电商短视频"
        return "\n".join(parts) or "请生成一张电商图片"


# ---------------------------------------------------------------------------
# StepHandler protocol + registry
# ---------------------------------------------------------------------------

class StepResult(TypedDict):
    status: str  # ok | error
    data: dict
    artifacts: list[str]
    error: str


class StepHandler(Protocol):
    async def run(
        self, input_data: dict, config: dict, ctx: ExecutionContext,
    ) -> StepResult:
        ...


_STEP_HANDLERS: dict[str, StepHandler] = {}


def register_step(name: str):
    """Decorator to register a StepHandler."""
    def decorator(cls: type) -> type:
        _STEP_HANDLERS[name] = cls()
        return cls
    return decorator


@register_step("optimize_prompt")
class OptimizePromptStep:
    """Optional prompt rewrite via host brain.

    If the plugin has not been granted ``brain.access`` permission, ``ctx.brain``
    is ``None``; in that case we keep the original prompt and continue rather
    than failing the whole pipeline.
    """

    async def run(self, input_data: dict, config: dict, ctx: ExecutionContext) -> StepResult:
        system = config.get("system_prompt", "你是电商内容创意专家，请优化以下提示词。")
        user_msg = input_data.get("prompt", "")
        if ctx.brain is None:
            logger.info(
                "optimize_prompt skipped: brain.access not granted, keeping original prompt"
            )
            return {"status": "ok", "data": dict(input_data), "artifacts": [], "error": ""}
        try:
            if hasattr(ctx.brain, "think_lightweight"):
                result = await ctx.brain.think_lightweight(prompt=user_msg, system=system)
            else:
                result = await ctx.brain.think(prompt=user_msg, system=system)
            optimized = _extract_text(result).strip() or user_msg
            return {"status": "ok", "data": {**input_data, "prompt": optimized}, "artifacts": [], "error": ""}
        except Exception as e:
            logger.warning("optimize_prompt failed (%s); falling back to original prompt", e)
            return {"status": "ok", "data": dict(input_data), "artifacts": [], "error": ""}


@register_step("generate_image")
class GenerateImageStep:
    """Pipeline step: create async DashScope task and wait in place for image_urls.

    Pipeline followers (e.g. stitch_images, translate) need real URLs, so we must
    block until the task finishes before returning.
    """

    POLL_INTERVAL_S = 3.0
    MAX_WAIT_S = 180.0

    async def run(self, input_data: dict, config: dict, ctx: ExecutionContext) -> StepResult:
        model = config.get("model") or resolve_model(input_data, ctx)
        prompt = input_data.get("prompt", "")

        force_ratio = config.get("force_ratio")
        if force_ratio:
            resolution = input_data.get("size") or (ctx.defaults or {}).get("default_image_size") or "2K"
            if resolution in RATIO_SIZE_MAP and force_ratio in RATIO_SIZE_MAP[resolution]:
                size = RATIO_SIZE_MAP[resolution][force_ratio]
            else:
                size = resolve_size(input_data, ctx)
        else:
            size = input_data.get("size") or resolve_size(input_data, ctx)

        _, asset_params = split_params(ctx.feature, input_data)
        resolved = await resolve_assets(asset_params, ctx.task_manager)

        if not resolved and input_data.get("ref_image"):
            resolved = await resolve_assets(
                {"ref_image": input_data["ref_image"]}, ctx.task_manager,
            )

        try:
            logger.debug(
                "GenerateImageStep: model=%s images_keys=%s n=%s",
                model, list(resolved.keys()) if resolved else "none",
                int(input_data.get("section_count") or config.get("n", 1)),
            )
            sem = ctx.semaphore or asyncio.Semaphore(10)
            async with sem:
                result = await ctx.dashscope.generate(
                    model=model,
                    prompt=prompt,
                    images=resolved,
                    capability=config.get("capability", "multimodal"),
                    size=size,
                    n=int(input_data.get("section_count") or config.get("n", 1)),
                )
            task_id = result.get("task_id", "") or ""
            urls = result.get("image_urls", []) or []

            if not urls and task_id:
                urls = await self._wait_for_urls(ctx, task_id)

            if not urls:
                return {
                    "status": "error", "data": input_data, "artifacts": [],
                    "error": "DashScope task produced no images",
                }

            return {
                "status": "ok",
                "data": {**input_data, "image_urls": urls, "api_task_id": task_id},
                "artifacts": urls,
                "error": "",
            }
        except Exception as e:
            return {"status": "error", "data": input_data, "artifacts": [], "error": str(e)}

    async def _wait_for_urls(self, ctx: ExecutionContext, task_id: str) -> list[str]:
        elapsed = 0.0
        while elapsed < self.MAX_WAIT_S:
            await asyncio.sleep(self.POLL_INTERVAL_S)
            elapsed += self.POLL_INTERVAL_S
            try:
                poll = await ctx.dashscope.get_task_result(task_id)
            except Exception as e:
                logger.debug("generate_image poll error %s: %s", task_id, e)
                continue
            status = (poll.get("status") or "").upper()
            if status == "SUCCEEDED":
                return poll.get("image_urls", []) or []
            if status == "FAILED":
                raise RuntimeError(poll.get("error") or "DashScope task failed")
        raise TimeoutError(
            f"DashScope task {task_id} did not finish in {int(self.MAX_WAIT_S)}s"
        )


@register_step("generate_video")
class GenerateVideoStep:
    """Pipeline step: create async Ark task.

    Unlike image generation, video tasks take minutes. This step submits
    the task and returns immediately with a pending task_id.  The plugin's
    background ``_poll_ark_tasks`` loop picks it up from there.
    """

    async def run(self, input_data: dict, config: dict, ctx: ExecutionContext) -> StepResult:
        model = config.get("model") or resolve_model(input_data, ctx)

        segments = input_data.get("segments")
        if segments and isinstance(segments, list) and len(segments) > 0:
            prompt_parts = []
            for seg in segments:
                if isinstance(seg, dict) and seg.get("prompt"):
                    prompt_parts.append(seg["prompt"].strip())
                elif isinstance(seg, str) and seg.strip():
                    prompt_parts.append(seg.strip())
            prompt = "\n".join(prompt_parts) if prompt_parts else ""
        else:
            prompt = input_data.get("prompt", "")

        if not prompt or not prompt.strip():
            return {
                "status": "error", "data": input_data, "artifacts": [],
                "error": "视频提示词为空，无法生成",
            }

        try:
            sem = ctx.semaphore or asyncio.Semaphore(10)
            async with sem:
                result = await ctx.ark.create_task(
                    model=model,
                    prompt=prompt,
                    images={},
                    **_build_video_kwargs(input_data, ctx),
                )
            task_id = result.get("task_id", "") or ""
            if not task_id:
                return {
                    "status": "error", "data": input_data, "artifacts": [],
                    "error": "Ark create_task returned no task_id",
                }

            return {
                "status": "ok",
                "data": {**input_data, "api_task_id": task_id, "video_url": ""},
                "artifacts": [],
                "error": "",
            }
        except Exception as e:
            return {"status": "error", "data": input_data, "artifacts": [], "error": str(e)}


@register_step("stitch_images")
class StitchImagesStep:
    async def run(self, input_data: dict, config: dict, ctx: ExecutionContext) -> StepResult:
        from PIL import Image
        import io
        import httpx

        urls = input_data.get("image_urls", [])
        if not urls:
            return {"status": "error", "data": input_data, "artifacts": [], "error": "No images to stitch"}

        images: list[Image.Image] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for url in urls:
                resp = await client.get(url)
                resp.raise_for_status()
                images.append(Image.open(io.BytesIO(resp.content)))

        direction = config.get("direction", "vertical")
        if direction == "vertical":
            target_w = max(img.width for img in images)
            resized: list[Image.Image] = []
            for img in images:
                if img.width != target_w:
                    ratio = target_w / img.width
                    img = img.resize(
                        (target_w, int(img.height * ratio)), Image.LANCZOS,
                    )
                resized.append(img)
            total_h = sum(img.height for img in resized)
            canvas = Image.new("RGB", (target_w, total_h))
            y = 0
            for img in resized:
                canvas.paste(img, (0, y))
                y += img.height
        else:
            target_h = max(img.height for img in images)
            resized = []
            for img in images:
                if img.height != target_h:
                    ratio = target_h / img.height
                    img = img.resize(
                        (int(img.width * ratio), target_h), Image.LANCZOS,
                    )
                resized.append(img)
            total_w = sum(img.width for img in resized)
            canvas = Image.new("RGB", (total_w, target_h))
            x = 0
            for img in resized:
                canvas.paste(img, (x, 0))
                x += img.width

        data_dir = ctx.plugin_api.get_data_dir()
        out_dir = data_dir / "stitched"
        out_dir.mkdir(parents=True, exist_ok=True)
        import uuid
        out_path = out_dir / f"{uuid.uuid4().hex[:8]}_stitched.png"
        canvas.save(str(out_path), quality=95)

        local_path = str(out_path)
        return {
            "status": "ok",
            "data": {
                **input_data,
                "stitched_path": local_path,
                "image_urls": [local_path],
                "local_paths": [local_path],
            },
            "artifacts": [local_path],
            "error": "",
        }


@register_step("concat_videos")
class ConcatVideosStep:
    async def run(self, input_data: dict, config: dict, ctx: ExecutionContext) -> StepResult:
        if not shutil.which("ffmpeg"):
            paths = input_data.get("video_paths", [])
            return {
                "status": "ok",
                "data": input_data,
                "artifacts": paths,
                "error": "ffmpeg not available, returning individual clips",
            }
        # TODO: implement ffmpeg concat in polish phase
        return {"status": "ok", "data": input_data, "artifacts": [], "error": ""}


@register_step("decompose_storyboard")
class DecomposeStoryboardStep:
    async def run(self, input_data: dict, config: dict, ctx: ExecutionContext) -> StepResult:
        script = input_data.get("storyboard_script", "") or input_data.get("prompt", "")
        if not script or not script.strip():
            return {"status": "error", "data": input_data, "artifacts": [],
                    "error": "故事脚本为空，请填写故事脚本"}
        total_duration = int(input_data.get("total_duration", 60))
        segment_duration = int(config.get("segment_duration", 10))

        if ctx.brain is None:
            logger.info("decompose_storyboard: brain unavailable, creating single segment")
            segments = [{"prompt": script.strip(), "duration": min(total_duration, segment_duration)}]
            return {
                "status": "ok",
                "data": {**input_data, "segments": segments},
                "artifacts": [], "error": "",
            }

        system_prompt = (
            f"你是专业的 AI 视频分镜师。将故事拆解为多段视频分镜脚本，"
            f"每段约 {segment_duration} 秒，总时长约 {total_duration} 秒。\n"
            f"每段 prompt 必须是具体的画面描述（镜头运动+场景+动作），而非旁白文字。\n"
            f"只输出 JSON，不要任何其他文字：\n"
            f"{{\"segments\": [{{\"prompt\": \"...\", \"duration\": {segment_duration}}}]}}"
        )
        try:
            if hasattr(ctx.brain, "think_lightweight"):
                result = await ctx.brain.think_lightweight(
                    prompt=script, system=system_prompt, max_tokens=4096,
                )
            else:
                result = await ctx.brain.think(prompt=script, system=system_prompt)
            import json
            import re
            text = _extract_text(result).strip()
            if not text:
                raise ValueError("LLM 返回空内容")
            fence = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
            if fence:
                text = fence.group(1).strip()
            if not text.startswith("{"):
                idx = text.find("{")
                if idx >= 0:
                    text = text[idx:]
            last = text.rfind("}")
            if last > 0:
                text = text[: last + 1]
            data = json.loads(text)
            segments = data.get("segments", [])
            if not segments:
                raise ValueError("分镜解析结果为空")
            return {
                "status": "ok",
                "data": {**input_data, "segments": segments},
                "artifacts": [],
                "error": "",
            }
        except Exception as e:
            return {"status": "error", "data": input_data, "artifacts": [], "error": str(e)}


_LANG_NAMES: dict[str, str] = {
    "zh": "中文",
    "zh-cn": "中文",
    "zh-CN": "中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "es": "西班牙文",
    "fr": "法文",
    "de": "德文",
    "ar": "阿拉伯文",
    "ru": "俄文",
    "pt": "葡萄牙文",
}


def _lang_label(code: str) -> str:
    if not code:
        return "英文"
    return _LANG_NAMES.get(code, _LANG_NAMES.get(code.lower(), code))


@register_step("llm_translate")
class LlmTranslateStep:
    """Translate prompt for downstream image gen.

    Two modes:
    1. Image-translate use case (default): there's a ref_image but no
       ``source_text`` to translate.  We don't call any LLM (would 400 on empty
       user message); instead we synthesize a multimodal-friendly prompt that
       instructs the image model to redraw the image with all on-image text
       translated into ``target_language``.
    2. Text-translate use case: ``source_text`` is non-empty.  We call the
       host brain if ``brain.access`` is granted; otherwise we fall back to
       returning the source text unchanged so the pipeline can still proceed.
    """

    async def run(self, input_data: dict, config: dict, ctx: ExecutionContext) -> StepResult:
        target_lang_code = (
            input_data.get("target_language")
            or config.get("target_language")
            or "en"
        )
        lang_label = _lang_label(target_lang_code)
        source_text = (input_data.get("source_text") or "").strip()
        user_prompt = (input_data.get("prompt") or "").strip()

        if not source_text:
            extra = f"，{user_prompt}" if user_prompt else ""
            synth = (
                f"根据这张图片，生成一张相同设计的{lang_label}版本。"
                f"要求：将图中所有文字替换为对应的{lang_label}翻译，"
                f"保持完全相同的版式布局、颜色方案、字体风格、背景设计和装饰元素。"
                f"翻译后的{lang_label}文字必须清晰可读、语法正确、排版位置与原图一致。"
                f"品牌名称和Logo可保留原文"
                f"{extra}"
            )
            return {
                "status": "ok",
                "data": {**input_data, "prompt": synth, "target_language": target_lang_code},
                "artifacts": [],
                "error": "",
            }

        if ctx.brain is None:
            logger.info(
                "llm_translate: brain.access not granted, passing source_text through unchanged"
            )
            return {
                "status": "ok",
                "data": {**input_data, "prompt": source_text, "translated_text": source_text},
                "artifacts": [],
                "error": "",
            }

        system = f"将用户输入的文本翻译为{lang_label}，只输出译文，不要解释。"
        try:
            if hasattr(ctx.brain, "think_lightweight"):
                result = await ctx.brain.think_lightweight(prompt=source_text, system=system)
            else:
                result = await ctx.brain.think(prompt=source_text, system=system)
            translated = _extract_text(result).strip() or source_text
            return {
                "status": "ok",
                "data": {**input_data, "prompt": translated, "translated_text": translated},
                "artifacts": [],
                "error": "",
            }
        except Exception as e:
            logger.warning("llm_translate failed (%s); falling back to source text", e)
            return {
                "status": "ok",
                "data": {**input_data, "prompt": source_text, "translated_text": source_text},
                "artifacts": [],
                "error": "",
            }


# ---------------------------------------------------------------------------
# PipelineStrategy
# ---------------------------------------------------------------------------

class PipelineStrategy(ExecutionStrategy):
    """Multi-step sequential pipeline with progress tracking."""

    async def execute(self, params: dict, ctx: ExecutionContext) -> dict:
        steps = ctx.feature.execution_config.get("steps", [])
        on_error = ctx.feature.execution_config.get("on_step_error", "abort")
        total = len(steps)

        task = await ctx.task_manager.create_task(
            feature_id=ctx.feature.id,
            module=ctx.feature.module,
            task_type="video" if ctx.feature.api_provider == "ark" else "image",
            api_provider=ctx.feature.api_provider,
            status="running",
            prompt=params.get("prompt", ""),
            model=resolve_model(params, ctx),
            execution_mode="pipeline",
            params=params,
            progress_current=0,
            progress_total=total,
        )

        context_data = dict(params)
        for i, step in enumerate(steps):
            action = step.get("action", "")
            handler = _STEP_HANDLERS.get(action)
            if not handler:
                await ctx.task_manager.update_task_status(
                    task["id"], "failed",
                    error_message=f"Unknown pipeline step: {action}",
                    failed_at_step=i,
                )
                return task

            try:
                result = await handler.run(context_data, step.get("config", {}), ctx)
            except Exception as e:
                await ctx.task_manager.update_task_status(
                    task["id"], "failed",
                    error_message=f"Step '{action}' exception: {e}",
                    failed_at_step=i,
                )
                return task

            if result["status"] == "error":
                if on_error == "abort":
                    await ctx.task_manager.update_task_status(
                        task["id"], "failed",
                        error_message=f"Step '{action}' failed: {result.get('error', '')}",
                        failed_at_step=i,
                    )
                    return task
                # on_error == "skip": continue to next step

            context_data = result["data"]
            await ctx.task_manager.update_task(
                task["id"], progress_current=i + 1,
            )
            try:
                ctx.plugin_api.broadcast_ui_event(
                    "task_update",
                    {"task_id": task["id"], "progress": i + 1, "total": total},
                )
            except Exception:
                pass

        image_urls = context_data.get("image_urls", [])
        local_paths = context_data.get("local_paths", [])
        video_url = context_data.get("video_url", "")
        api_task_id = context_data.get("api_task_id", "")

        updates: dict[str, Any] = {"progress_current": total}
        if image_urls:
            import json
            updates["image_urls"] = json.dumps(image_urls)
        if local_paths:
            import json as _jl
            updates["local_paths"] = _jl.dumps(local_paths)
        if video_url:
            updates["video_url"] = video_url
        if api_task_id:
            updates["api_task_id"] = api_task_id

        if api_task_id and not (image_urls or video_url):
            await ctx.task_manager.update_task(task["id"], **updates)
        else:
            await ctx.task_manager.update_task_status(
                task["id"], "succeeded", **updates,
            )
        return task


# ---------------------------------------------------------------------------
# BatchStrategy
# ---------------------------------------------------------------------------

class BatchStrategy(ExecutionStrategy):
    """Run the same operation per image, using parent-child task structure.

    ``variation_source`` points to a param key whose value is a list of asset
    IDs.  Each asset becomes an independent child task under one parent.
    """

    async def execute(self, params: dict, ctx: ExecutionContext) -> dict:
        cfg = ctx.feature.execution_config
        variation_key = cfg.get("variation_source", "variations")
        asset_ids: list[str] = params.get(variation_key, [])
        max_concurrent = int(cfg.get("max_concurrent", 4))

        if not asset_ids or not isinstance(asset_ids, list):
            base = strategy_factory(cfg.get("base_strategy", "prompt_template"))
            return await base.execute(params, ctx)

        total = len(asset_ids)
        chosen_model = resolve_model(params, ctx)

        parent = await ctx.task_manager.create_task(
            feature_id=ctx.feature.id,
            module=ctx.feature.module,
            task_type="video" if ctx.feature.api_provider == "ark" else "image",
            api_provider=ctx.feature.api_provider,
            status="running",
            prompt=params.get("prompt", ""),
            model=chosen_model,
            execution_mode="batch",
            params=params,
            progress_current=0,
            progress_total=total,
        )

        text_params, _ = split_params(ctx.feature, params)
        prompt = safe_format(ctx.feature.prompt_template or "", text_params)
        size = resolve_size(params, ctx)
        sem = asyncio.Semaphore(max_concurrent)

        async def run_one(asset_id: str) -> None:
            try:
                resolved = await resolve_assets(
                    {"ref_image": asset_id}, ctx.task_manager,
                )
                async with sem:
                    if ctx.feature.api_provider == "dashscope":
                        api_result = await ctx.dashscope.generate(
                            model=chosen_model,
                            prompt=prompt,
                            images=resolved,
                            capability=ctx.feature.api_capability,
                            size=size,
                            n=1,
                        )
                    else:
                        api_result = await ctx.ark.create_task(
                            model=chosen_model,
                            prompt=prompt,
                            images=resolved,
                            **_build_video_kwargs(params, ctx),
                        )
                child = await _persist_api_result(
                    ctx, api_result, prompt=prompt, model=chosen_model,
                    execution_mode="batch", params=params,
                )
                await ctx.task_manager.update_task(
                    child["id"], batch_parent_id=parent["id"],
                )
                if child.get("status") == "succeeded":
                    try:
                        ctx.plugin_api.broadcast_ui_event(
                            "task_update",
                            {"task_id": child["id"], "status": "succeeded"},
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Batch child failed: %s", e)

            current = await ctx.task_manager.increment_progress(parent["id"])
            try:
                ctx.plugin_api.broadcast_ui_event(
                    "task_update",
                    {"task_id": parent["id"], "progress": current, "total": total},
                )
            except Exception:
                pass

        await asyncio.gather(*(run_one(aid) for aid in asset_ids))

        await ctx.task_manager.recompute_batch_parent_status(parent["id"])
        parent = await ctx.task_manager.get_task(parent["id"]) or parent
        return parent


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_STRATEGY_MAP: dict[str, type[ExecutionStrategy]] = {
    "prompt_template": PromptTemplateStrategy,
    "agent": AgentStrategy,
    "pipeline": PipelineStrategy,
    "batch": BatchStrategy,
}


def strategy_factory(mode: str) -> ExecutionStrategy:
    cls = _STRATEGY_MAP.get(mode)
    if not cls:
        raise ValueError(f"Unknown execution mode: {mode}")
    return cls()
