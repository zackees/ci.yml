"""One age boundary for cached passes and transported execution evidence."""
from __future__ import annotations

import math

DEFAULT_MAX_AGE_HOURS = 24.0
CLOCK_SKEW_SECONDS = 60.0


def fresh(passed_at: float, now: float, max_age_seconds: float, *,
          clock_skew_seconds: float = CLOCK_SKEW_SECONDS) -> bool:
    """Preserve the original execution time; never refresh it on reuse."""
    try:
        return (all(math.isfinite(value) for value in (passed_at, now, max_age_seconds, clock_skew_seconds))
                and passed_at > 0 and max_age_seconds > 0 and clock_skew_seconds >= 0
                and -clock_skew_seconds <= now - passed_at <= max_age_seconds)
    except OverflowError:
        return False
