"""OCR worker boundaries: single-writer lock, budgets, and offline weight pins.

Nothing in this package starts an engine. It constrains *how* an engine worker may
run, so that the constraints can be tested without a GPU or a model download.
"""

from __future__ import annotations

from .limits import (
    DEFAULT_BUDGET,
    WorkerBudget,
    WorkerLock,
    verify_weights,
)

__all__ = ["DEFAULT_BUDGET", "WorkerBudget", "WorkerLock", "verify_weights"]
