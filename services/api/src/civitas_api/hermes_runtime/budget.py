"""Bounded turn budget adapted from Hermes Agent's iteration_budget.py.

Copyright (c) 2025 Nous Research. Licensed under the MIT License. See
``NOTICE-HERMES-MIT.txt`` and ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import math
import threading


def normalize_budget_warning_ratio(value: object) -> float | None:
    """Return a finite warning ratio strictly between zero and one."""

    if value is None or isinstance(value, bool):
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return ratio if math.isfinite(ratio) and 0 < ratio < 1 else None


class IterationBudget:
    """Thread-safe bounded counter for one civic agent turn."""

    def __init__(self, max_total: int):
        self.max_total = max(1, int(max_total))
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)
