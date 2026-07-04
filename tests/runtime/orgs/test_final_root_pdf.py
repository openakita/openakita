"""图3 final-PDF: the polished 主编 report is rendered at command convergence
(``finalize_command_project``) from the LAST-recorded root deliverable — not at
the first root finish — so the pdf always matches the final integrated .md.

These pin the SCHEDULING contract (deterministic, no Chromium):

* a recorded root artifact + ok convergence -> ``_maybe_render_root_pdf`` is
  scheduled exactly once with that artifact, and the record is consumed;
* a failed convergence (ok=False) still consumes the record (no leak) but
  renders nothing;
* a missing record is a graceful no-op (the .md remains the delivery).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openakita.orgs.runtime import OrgRuntime


class _Org:
    def __init__(self, org_id: str) -> None:
        self.id = org_id
        self.state = "active"


class _Lookup:
    def get_org(self, org_id: str) -> Any:
        return _Org(org_id)


def _make_runtime() -> OrgRuntime:
    return OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
    )


def _patch_capture(rt: OrgRuntime) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _fake_render(**kwargs: Any) -> None:
        calls.append(kwargs)

    rt._maybe_render_root_pdf = _fake_render  # type: ignore[method-assign]
    return calls


@pytest.mark.asyncio
async def test_finalize_renders_final_root_pdf_from_recorded_artifact() -> None:
    rt = _make_runtime()
    calls = _patch_capture(rt)
    rt._root_final_artifact = {"cmd-1": ("editor-in-chief", "/tmp/final_report.md")}

    rt.finalize_command_project("org-x", "cmd-1", ok=True)
    await asyncio.sleep(0)  # let the scheduled task run

    assert len(calls) == 1
    assert calls[0]["artifact_path"] == "/tmp/final_report.md"
    assert calls[0]["node_id"] == "editor-in-chief"
    assert calls[0]["command_id"] == "cmd-1"
    # record consumed so a later finalize can't double-render.
    assert "cmd-1" not in rt._root_final_artifact


@pytest.mark.asyncio
async def test_finalize_failed_consumes_record_without_rendering() -> None:
    rt = _make_runtime()
    calls = _patch_capture(rt)
    rt._root_final_artifact = {"cmd-2": ("root", "/tmp/x.md")}

    rt.finalize_command_project("org-x", "cmd-2", ok=False)
    await asyncio.sleep(0)

    assert calls == []
    # still cleaned up (no leak) even though we didn't render.
    assert "cmd-2" not in rt._root_final_artifact


@pytest.mark.asyncio
async def test_finalize_without_record_is_noop() -> None:
    rt = _make_runtime()
    calls = _patch_capture(rt)

    rt.finalize_command_project("org-x", "cmd-missing", ok=True)
    await asyncio.sleep(0)

    assert calls == []
