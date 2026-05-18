"""Branch / routing node — picks a downstream edge.

Per ADR-0007, a ``ConditionNode`` lets a template encode "after X
finishes, go to Y or Z based on Y(...)" without burning an LLM call.
The node:

* runs a synchronous or asynchronous predicate over the delegation
  payload,
* returns a :class:`DelegationResult` whose ``message`` is the chosen
  branch label and whose ``metadata["next_address"]`` carries the
  routing target (a node id, role, or workbench address). The
  supervisor consumes this hint via :class:`runtime.state_graph.StateGraph`
  (see ADR-0007 and ``runtime/state_graph.py``); when the template
  declares formal conditional edges from this node, the StateGraph's
  router takes precedence over the hint,
* never calls a tool and never speaks to an LLM.

The predicate is supplied by the template author. For the most
common shapes (key match, threshold, regex) the registry layer in
Phase 5 will provide reusable factories; here we keep the node free
of any DSL.

Inputs to the predicate come from three places, in priority order:

1. ``msg.metadata`` — already-typed dict the supervisor passes when
   it knows the structure (e.g. preceding node's ``data``).
2. Parsed JSON from the instruction body, if it is a JSON object.
3. Raw instruction string under the synthetic key ``"text"``.

Predicates return a string label that MUST match one of the
node's declared ``branches``. Returning anything else triggers a
deterministic failure (no silent fall-through), which the
supervisor surfaces and the stall detector accounts for.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..messenger import NodeMessage
from ..supervisor import DelegationResult
from .base import BaseNode, NodeContext

__all__ = [
    "BranchInputs",
    "ConditionNode",
    "ConditionPredicate",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Predicate signature
# ---------------------------------------------------------------------------


BranchInputs = Mapping[str, Any]
"""Read-only view of the inputs handed to a predicate."""


ConditionPredicate = Callable[[BranchInputs], "str | Awaitable[str]"]
"""``predicate(inputs) -> branch_label`` (sync or async)."""


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class ConditionNode(BaseNode):
    """Routes the conversation to the named branch the predicate picks."""

    node_type = "condition"

    def __init__(
        self,
        *,
        node_id: str,
        org_id: str,
        predicate: ConditionPredicate,
        branches: Mapping[str, str],
        role: str | None = None,
    ) -> None:
        super().__init__(node_id=node_id, org_id=org_id, role=role)
        if not branches:
            raise ValueError("ConditionNode requires at least one branch")
        for label, target in branches.items():
            if not isinstance(label, str) or not label:
                raise ValueError(
                    f"branch label must be a non-empty string, got {label!r}"
                )
            if not isinstance(target, str) or not target:
                raise ValueError(
                    f"branch target for {label!r} must be a non-empty string"
                )
        self._predicate = predicate
        self._branches: dict[str, str] = dict(branches)

    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        ctx.cancel_token.raise_if_cancelled()
        inputs = self._build_inputs(msg)
        outcome = self._predicate(inputs)
        if inspect.isawaitable(outcome):
            label = await outcome
        else:
            label = outcome  # type: ignore[assignment]
        if not isinstance(label, str):
            return self._fail(
                msg,
                "predicate returned non-string label",
                {"raw_label": repr(label)},
            )
        if label not in self._branches:
            return self._fail(
                msg,
                f"predicate returned unknown branch label {label!r}",
                {"valid": sorted(self._branches.keys())},
            )
        target = self._branches[label]
        await ctx.stream.emit(
            "updates",
            "branch_selected",
            {
                "node_id": self.node_id,
                "label": label,
                "next_address": target,
                "correlation_id": msg.correlation_id,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
        return DelegationResult(
            success=True,
            speaker=self.node_id,
            message=label,
            metadata={
                "correlation_id": msg.correlation_id,
                "next_address": target,
                "branch_label": label,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_inputs(msg: NodeMessage) -> BranchInputs:
        inputs: dict[str, Any] = {}
        text = (msg.instruction or "").strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    inputs.update(parsed)
            except json.JSONDecodeError:
                logger.debug(
                    "ConditionNode: instruction looked like JSON but failed to parse"
                )
        inputs["text"] = text
        if msg.metadata:
            inputs.update(msg.metadata)
        return inputs

    def _fail(
        self, msg: NodeMessage, reason: str, extra: Mapping[str, Any]
    ) -> DelegationResult:
        return DelegationResult(
            success=False,
            speaker=self.node_id,
            message=reason,
            metadata={
                "correlation_id": msg.correlation_id,
                **extra,
            },
        )
