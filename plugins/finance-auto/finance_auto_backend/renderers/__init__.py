"""ReportRenderer subpackage (v0.3 Part Infra section 1, M1 W2 Stage 2).

The dual-track design: xltpl handles static reports (BS / PL / OE etc., row
counts capped at ~50) by pre-flattening rows into ``rows[0..N-1]`` slots in a
hand-built Excel template; openpyxl handles dynamic detail tables (AR / AP
breakdowns, audit work-papers, customer-level notes) by directly writing
cells and copying a sample row's styling.

Public API: :func:`make_renderer` returns a concrete :class:`ReportRenderer`
based on the requested report kind + estimated row count.  Callers never
import ``XltplRenderer`` / ``OpenpyxlDirectRenderer`` directly; the factory
is the single integration point per Part Biz contract C1.
"""

from .base import RenderResult, ReportRenderer
from .factory import make_renderer
from .openpyxl_writer import OpenpyxlDirectRenderer
from .simplifier import (
    DetailRow,
    SimplifyConfig,
    SimplifyResult,
    simplify_aux_details,
)
from .xltpl_renderer import XltplRenderer

__all__ = [
    "DetailRow",
    "OpenpyxlDirectRenderer",
    "RenderResult",
    "ReportRenderer",
    "SimplifyConfig",
    "SimplifyResult",
    "XltplRenderer",
    "make_renderer",
    "simplify_aux_details",
]
