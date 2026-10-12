"""Eval harness (SIB-81): measure repair quality against a corpus of failure cases.

``python -m sbflow_worker.evalkit`` (``make eval``) runs every case under
``eval/cases/`` and writes a JSON report plus a one-screen summary. See
``eval/README.md`` for the case format and how to read the numbers.
"""

from .case import CaseError, EvalCase, discover, load_case
from .report import CaseResult, render_text, score_repair, summarize

__all__ = [
    "CaseError",
    "CaseResult",
    "EvalCase",
    "discover",
    "load_case",
    "render_text",
    "score_repair",
    "summarize",
]
