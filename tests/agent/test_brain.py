"""V2-only smoke test for :class:`openakita.agent.brain.Brain`.

N-G7-1 (P-RC-7 G-RC-7 audit nit): the three deleted parity files
(``test_brain_parity.py`` / ``test_context_parity.py`` /
``test_tools_parity.py``) used to pin
``brain.get_current_endpoint_info()`` against the legacy shim. With
the shim deleted at P7.14 the v1-vs-v2 comparison is now
tautological, but we still want one fast smoke that builds a real
:class:`openakita.agent.brain.Brain` against a stub LLMClient and
asserts the canonical ``{name, model, healthy}`` endpoint-info shape.

This is the v2-only re-statement of the prior N6 parity case at
:func:`tests/parity/test_brain_parity.py::test_failover_endpoint_info_parity`
(commit ``5906b606``), trimmed to ~30 LOC.
"""

from __future__ import annotations

from openakita.agent.brain import Brain
from openakita.runtime.llm import EndpointFailoverView


class _StubLLMEndpoint:
    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model


class _StubLLMClient:
    """Mirror enough of :class:`openakita.llm.client.LLMClient` to drive
    :class:`EndpointFailoverView.current_endpoint_info` without touching
    settings, providers, or compiler endpoints.
    """

    def __init__(self) -> None:
        self.endpoints = [
            _StubLLMEndpoint(name="primary", model="m-1"),
            _StubLLMEndpoint(name="secondary", model="m-2"),
        ]

        class _Provider:
            def __init__(self, model: str, is_healthy: bool) -> None:
                self.model = model
                self.is_healthy = is_healthy

        self.providers = {
            "primary": _Provider("m-1", is_healthy=True),
            "secondary": _Provider("m-2", is_healthy=False),
        }


def test_brain_get_current_endpoint_info_smoke() -> None:
    """V2 Brain returns canonical ``{name, model, healthy}`` for primary."""
    brain = Brain.__new__(Brain)
    brain._llm_client = _StubLLMClient()
    brain._failover_view = EndpointFailoverView(brain._llm_client)
    assert brain.get_current_endpoint_info() == {
        "name": "primary",
        "model": "m-1",
        "healthy": True,
    }
