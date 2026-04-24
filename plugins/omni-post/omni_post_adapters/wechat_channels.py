"""WeChat Channels (视频号) adapter — shadow-root + micro-frontend.

The Channels creator UI (``channels.weixin.qq.com/platform/post/create``)
embeds a ``wujie-app`` micro-frontend whose root element has an **open**
shadow root. Inside that shadow root live the file input and the
caption editor; the "publish" button is also mounted under its own
shadow root further down the tree.

Plain Playwright CSS locators (``page.locator("input[type=file]")``)
resolve against the light DOM only, so on first run they silently miss
every element and the task times out. This is **MultiPost-Extension
issue #166** reproduced verbatim: the extension did not pierce shadow
roots and broke on every major Channels refresh.

Fix strategy
------------

For each step tagged ``pierce_shadow: true`` we:

1. Walk the DOM in a single ``page.evaluate`` pass, descending into
   every open shadow root we encounter (closed roots are invisible —
   Channels does not currently use them).
2. Resolve the CSS selector against every visited subtree and keep the
   **first** match.
3. Route the original action (click / type / upload / wait) to that
   element via native ``dispatchEvent`` (for click) or DataTransfer
   synthesis (for upload), since Playwright's higher-level APIs
   (``locator.click()``) still bind to the light DOM.

Steps without ``pierce_shadow`` fall through to the superclass
implementation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from omni_post_models import ErrorKind, OmniPostError

logger = logging.getLogger("openakita.plugins.omni-post")


# The shadow-piercing walker is short but has to be careful to bail out
# on cycles (iframes can reintroduce the same element via a detached
# document) — we limit depth to 12 shadow roots which is well beyond
# what any production portal actually uses.
_PIERCE_JS = r"""
(params) => {
    const { sel, maxDepth } = params;
    const visited = new WeakSet();
    function* descend(node, depth) {
        if (!node || depth > maxDepth) return;
        if (visited.has(node)) return;
        visited.add(node);
        yield node;
        const root = node.shadowRoot;
        if (root) yield* descend(root, depth + 1);
        const children = node.children || [];
        for (const c of children) yield* descend(c, depth);
    }
    for (const n of descend(document, 0)) {
        try {
            const hit = (n.querySelector ? n.querySelector(sel) : null);
            if (hit) {
                // Make the element addressable from the outside.
                hit.setAttribute("data-omni-post-target", "1");
                return { ok: true };
            }
        } catch (e) {
            // querySelector throws on invalid :has() in some browsers.
        }
    }
    return { ok: false };
}
"""


_CLICK_JS = r"""
() => {
    const t = document.querySelector('[data-omni-post-target="1"]');
    if (!t) return false;
    t.removeAttribute('data-omni-post-target');
    if (typeof t.click === 'function') t.click();
    t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    return true;
}
"""


def _fill_js() -> str:
    # Separate function so we can template the text safely via JSON in
    # the caller; the JSON is sent through page.evaluate's arg, not via
    # string interpolation, so there is no XSS / escaping concern.
    return r"""
        (text) => {
            const t = document.querySelector('[data-omni-post-target="1"]');
            if (!t) return false;
            t.removeAttribute('data-omni-post-target');
            if ('value' in t) {
                const proto = Object.getPrototypeOf(t);
                const setter = Object.getOwnPropertyDescriptor(proto, 'value');
                if (setter && setter.set) {
                    setter.set.call(t, text);
                } else {
                    t.value = text;
                }
                t.dispatchEvent(new Event('input', { bubbles: true }));
                t.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
            // contenteditable path
            t.focus();
            t.innerHTML = '';
            const node = document.createTextNode(text);
            t.appendChild(node);
            t.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }));
            return true;
        }
    """


# Late import — omni_post_engine_pw imports us via build_adapter, so we
# must defer to avoid circular imports.
def _load_generic_adapter():
    from omni_post_engine_pw import GenericJsonAdapter, _render_selector, _render_template

    return GenericJsonAdapter, _render_selector, _render_template


class WeChatChannelsAdapter:
    """Thin wrapper that delegates to GenericJsonAdapter but intercepts
    steps with ``pierce_shadow: true``.

    We deliberately compose rather than inherit so we can run the same
    shadow-piercing logic against *any* bundle in a future sprint — the
    goal is "make this one platform work today", not "ship a second
    DSL".
    """

    platform_id = "wechat_channels"

    def __init__(self, bundle: dict[str, Any]) -> None:
        self._bundle = bundle
        GenericJsonAdapter, _render_selector, _render_template = _load_generic_adapter()
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
                    error_message=f"wechat_channels goto failed: {e}",
                    screenshots=list(ctx.screenshots),
                )

        for i, step in enumerate(action.get("steps", [])):
            try:
                if step.get("pierce_shadow"):
                    await self._execute_pierced_step(ctx, step)
                else:
                    # Delegate non-pierced steps to the plain generic
                    # executor — reuses all its already-tested logic.
                    from omni_post_engine_pw import _execute_step

                    await _execute_step(ctx, step)
            except OmniPostError as e:
                return AdapterOutcome(
                    success=False,
                    error_kind=e.kind.value,
                    error_message=f"wechat_channels step {i} ({step.get('kind')}): {e}",
                    screenshots=list(ctx.screenshots),
                )
            except Exception as e:  # noqa: BLE001
                return AdapterOutcome(
                    success=False,
                    error_kind=ErrorKind.PLATFORM_BREAKING_CHANGE.value,
                    error_message=f"wechat_channels step {i} ({step.get('kind')}): {e}",
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

    async def _execute_pierced_step(self, ctx, step: dict) -> None:
        """Resolve the step's selector through shadow roots, then act."""

        page = ctx.page
        kind = step["kind"]
        optional = bool(step.get("optional", False))
        timeout_ms = int(step.get("timeout_ms", 15_000))
        selector = self._render_selector(step, ctx)

        if not selector and kind != "wait":
            raise OmniPostError(
                ErrorKind.PLATFORM_BREAKING_CHANGE,
                f"pierce_shadow step {kind!r} requires a selector",
            )

        # Retry the shadow walk until it finds a target or we time out.
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
        while True:
            try:
                hit = await page.evaluate(_PIERCE_JS, {"sel": selector, "maxDepth": 12})
            except Exception as e:  # noqa: BLE001
                if optional:
                    logger.debug("pierced step skipped: %s", e)
                    return
                raise OmniPostError(
                    ErrorKind.PLATFORM_BREAKING_CHANGE,
                    f"shadow walker errored on {selector}: {e}",
                ) from e
            if hit and hit.get("ok"):
                break
            if asyncio.get_event_loop().time() >= deadline:
                if optional:
                    return
                raise OmniPostError(
                    ErrorKind.PLATFORM_BREAKING_CHANGE,
                    f"selector {selector!r} not found in any open shadow root",
                )
            await asyncio.sleep(0.3)

        if kind == "wait":
            return
        if kind == "click":
            ok = await page.evaluate(_CLICK_JS)
            if not ok:
                raise OmniPostError(
                    ErrorKind.PLATFORM_BREAKING_CHANGE,
                    "pierced click target vanished before dispatch",
                )
            return
        if kind == "type":
            text = self._render_template(step.get("text", ""), ctx)
            ok = await page.evaluate(_fill_js(), text)
            if not ok:
                raise OmniPostError(
                    ErrorKind.PLATFORM_BREAKING_CHANGE,
                    "pierced type target vanished before fill",
                )
            return
        if kind == "upload":
            # Upload via file chooser — works even inside an open shadow
            # root since Playwright listens at the browser event layer.
            field = step.get("field", "asset_storage_path")
            file_path = getattr(ctx, field, None)
            if not file_path:
                raise OmniPostError(
                    ErrorKind.DEPENDENCY,
                    f"upload step requires non-empty field {field!r}",
                )
            async with page.expect_file_chooser() as fc_info:
                await page.evaluate(_CLICK_JS)
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(file_path))
            return

        raise OmniPostError(
            ErrorKind.PLATFORM_BREAKING_CHANGE,
            f"pierce_shadow does not support step kind {kind!r}",
        )


__all__ = ["WeChatChannelsAdapter"]
