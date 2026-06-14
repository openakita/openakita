"""omni-post cookie health probe — on-demand, Playwright-backed.

Background
----------

MultiPost-Extension issue #207 caught a pattern we want to avoid:
eager background polling of every account's cookies every few
minutes. It lit up the rate-limit alarms on most platforms, broke
cookies that were fine, and made debugging impossible because the
probe would race the user's manual publish.

This module supplies a *lazy* factory the pipeline can use to build a
probe function. Callers pass the probe function into
:meth:`CookiePool.probe_lazy`, which only runs when:

    * The Accounts tab is opened in the UI.
    * The user hits "Refresh health" on an account.
    * A publish's ``precheck`` stage runs right before the real pipeline.

There is NO timer, NO thread pool, NO "warm up the cache" in here — if
you find yourself adding one, stop and revisit issue #207.

The probe
---------

Given an engine handle and a selectors directory, we reuse the
``probe`` action declared in each platform's selector JSON
(``omni_post_selectors/<platform>.json``): open the target URL, wait
for the action's success selector, and report ``ok`` / ``cookie_expired``
based on the outcome.  The probe runs in its OWN browser context so
the main publish queue is never blocked or has its user_data_dir
raced.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from omni_post_adapters import load_selector_bundle

logger = logging.getLogger("openakita.plugins.omni-post")


ProbeFn = Callable[[str], Awaitable[str]]


def build_playwright_probe(
    *,
    engine: Any,
    selectors_dir: Path,
    platform_id: str,
    timeout_ms: int = 15_000,
) -> ProbeFn:
    """Return a one-shot probe function keyed to ``platform_id``.

    The returned coroutine takes a plaintext cookie blob and returns
    one of ``"ok"``, ``"cookie_expired"``, or ``"unknown"``. Any
    unexpected failure maps to ``"unknown"`` and is logged at WARNING
    so the UI can still render a soft "?" state instead of blowing up.
    """

    async def _probe(cookie_plaintext: str) -> str:
        try:
            bundle = load_selector_bundle(platform_id, selectors_dir)
        except FileNotFoundError:
            logger.warning("health probe: no bundle for %s", platform_id)
            return "unknown"

        action = bundle.get("actions", {}).get("probe") or bundle.get("actions", {}).get(
            "precheck"
        )
        if not action or not action.get("url"):
            logger.warning("health probe: %s has no probe/precheck action", platform_id)
            return "unknown"

        await engine.ensure_started()
        assert engine._browser is not None  # noqa: SLF001

        context = None
        try:
            context = await engine._browser.new_context()  # noqa: SLF001
            from omni_post_engine_pw import _inject_cookies

            await _inject_cookies(context, cookie_plaintext)
        except Exception as e:  # noqa: BLE001
            logger.warning("health probe: cookie injection failed: %s", e)
            if context is not None:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass
            return "cookie_expired"

        page = None
        try:
            page = await context.new_page()
            try:
                await page.goto(
                    action["url"], wait_until="domcontentloaded", timeout=timeout_ms
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("health probe goto %s failed: %s", action["url"], e)
                return "cookie_expired"

            selector = _first_selector(action.get("steps") or [])
            if not selector:
                return "ok"
            try:
                await page.wait_for_selector(selector, timeout=timeout_ms)
            except TimeoutError:
                return "cookie_expired"
            except Exception as e:  # noqa: BLE001
                logger.debug("health probe wait failed: %s", e)
                return "unknown"
            return "ok"
        finally:
            try:
                if page is not None:
                    await page.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if context is not None:
                    await context.close()
            except Exception:  # noqa: BLE001
                pass

    return _probe


def _first_selector(steps: list[dict]) -> str | None:
    """Pick the first ``wait`` or ``click`` step's selector.

    The probe action is defined as "open URL, wait for this selector".
    We trust the first selector that appears; platform authors who need
    a different heuristic should add a dedicated probe action (vs
    falling through to precheck).
    """

    for step in steps:
        sel = step.get("selector")
        if isinstance(sel, str) and sel.strip():
            return sel
    return None


__all__ = ["ProbeFn", "build_playwright_probe"]
