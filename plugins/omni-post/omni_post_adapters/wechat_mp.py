"""WeChat Official Accounts (mp.weixin.qq.com) adapter — iframe-aware.

The MP article editor lives inside a named iframe (``ueditor_0``) that
is itself loaded from a different origin. Any step targeting the rich-
text body needs to run against that frame rather than the top-level
document.

Playwright exposes ``page.frame_locator(sel)`` which returns a
``FrameLocator`` whose ``.locator()`` walks the iframe's DOM the same
way ``page.locator`` walks the page. We honour a ``frame`` field on
each step: when set, the step runs in that frame; when absent, it
behaves like a normal generic step (title input, "save draft" button,
etc. live on the top-level document).

We deliberately do NOT re-implement the generic step executor inside
this adapter — we simply re-route the locator base to the frame when
``frame`` is present and delegate the rest to the engine's standard
code path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from omni_post_models import ErrorKind, OmniPostError

logger = logging.getLogger("openakita.plugins.omni-post")


def _load_generic():
    from omni_post_engine_pw import (
        GenericJsonAdapter,
        _render_selector,
        _render_template,
    )

    return GenericJsonAdapter, _render_selector, _render_template


class WeChatMpAdapter:
    """Dispatches individual steps into an iframe when a ``frame`` key is set."""

    platform_id = "wechat_mp"

    def __init__(self, bundle: dict[str, Any]) -> None:
        self._bundle = bundle
        GenericJsonAdapter, _render_selector, _render_template = _load_generic()
        self._generic = GenericJsonAdapter(bundle)
        self._render_selector = _render_selector
        self._render_template = _render_template

    @property
    def bundle(self) -> dict[str, Any]:
        return self._bundle

    def resolve_action(self, action_name: str):
        return self._generic.resolve_action(action_name)

    async def precheck(self, ctx):
        return await self._run_action(ctx, "precheck")

    async def fill_form(self, ctx):
        return await self._run_action(ctx, "fill_form")

    async def submit(self, ctx):
        return await self._run_action(ctx, "submit")

    async def _run_action(self, ctx, action_name: str):
        from omni_post_adapters import AdapterOutcome

        action = self._generic.resolve_action(action_name)
        page = ctx.page
        url = action.get("url")
        timeout_ms = int(action.get("timeout_ms", 30_000))
        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as e:  # noqa: BLE001
                return AdapterOutcome(
                    success=False,
                    error_kind=ErrorKind.NETWORK.value,
                    error_message=f"wechat_mp goto failed: {e}",
                    screenshots=list(ctx.screenshots),
                )

        for i, step in enumerate(action.get("steps", [])):
            try:
                if step.get("frame"):
                    await self._execute_framed_step(ctx, step)
                else:
                    from omni_post_engine_pw import _execute_step

                    await _execute_step(ctx, step)
            except OmniPostError as e:
                return AdapterOutcome(
                    success=False,
                    error_kind=e.kind.value,
                    error_message=f"wechat_mp step {i} ({step.get('kind')}): {e}",
                    screenshots=list(ctx.screenshots),
                )
            except Exception as e:  # noqa: BLE001
                return AdapterOutcome(
                    success=False,
                    error_kind=ErrorKind.PLATFORM_BREAKING_CHANGE.value,
                    error_message=f"wechat_mp step {i} ({step.get('kind')}): {e}",
                    screenshots=list(ctx.screenshots),
                )

        published_url = None
        if action.get("capture_url_on_success"):
            try:
                published_url = page.url
            except Exception:  # noqa: BLE001
                published_url = None
        return AdapterOutcome(
            success=True,
            published_url=published_url,
            screenshots=list(ctx.screenshots),
        )

    async def _execute_framed_step(self, ctx, step: dict) -> None:
        page = ctx.page
        kind = step["kind"]
        optional = bool(step.get("optional", False))
        timeout_ms = int(step.get("timeout_ms", 15_000))
        frame_sel = str(step["frame"])
        selector = self._render_selector(step, ctx)

        frame_locator = page.frame_locator(frame_sel)

        try:
            if kind == "wait":
                if selector:
                    # FrameLocator has no wait_for_selector; use locator.wait_for.
                    await frame_locator.locator(selector).first.wait_for(timeout=timeout_ms)
                else:
                    await asyncio.sleep(float(step.get("seconds", 1.0)))
                return
            if kind == "click":
                locator = frame_locator.locator(selector)
                await locator.first.wait_for(timeout=timeout_ms)
                await locator.first.click()
                return
            if kind == "type":
                locator = frame_locator.locator(selector)
                await locator.first.wait_for(timeout=timeout_ms)
                text = self._render_template(step.get("text", ""), ctx)
                if step.get("clear", False):
                    await locator.first.fill("")
                await locator.first.fill(text)
                return
            if kind == "upload":
                field = step.get("field", "asset_storage_path")
                file_path = getattr(ctx, field, None)
                if not file_path:
                    raise OmniPostError(
                        ErrorKind.DEPENDENCY,
                        f"upload step requires non-empty field {field!r}",
                    )
                locator = frame_locator.locator(selector)
                await locator.first.wait_for(timeout=timeout_ms)
                await locator.first.set_input_files(str(file_path))
                return
            if kind == "assert_url":
                expected = self._render_template(step.get("pattern", ""), ctx)
                if not expected:
                    return
                pattern = re.compile(expected)
                current = page.url
                if not pattern.search(current):
                    raise OmniPostError(
                        ErrorKind.COOKIE_EXPIRED
                        if step.get("on_fail") == "cookie_expired"
                        else ErrorKind.UNKNOWN,
                        f"url {current!r} does not match {expected!r}",
                    )
                return
        except OmniPostError:
            raise
        except Exception as e:
            if optional:
                logger.debug("framed step %s skipped: %s", kind, e)
                return
            raise OmniPostError(
                ErrorKind.PLATFORM_BREAKING_CHANGE,
                f"framed step {kind} failed against {frame_sel}: {e}",
            ) from e

        raise OmniPostError(
            ErrorKind.PLATFORM_BREAKING_CHANGE,
            f"wechat_mp frame step kind {kind!r} is not supported",
        )


__all__ = ["WeChatMpAdapter"]
