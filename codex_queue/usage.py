"""Fail-closed parsing of documented account/rateLimits/read snapshots."""
from dataclasses import dataclass
import math


class UsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Window:
    used: float
    minutes: int
    reset: int


@dataclass(frozen=True)
class Usage:
    five: Window
    weekly: Window
    observed: float
    reached: bool = False

    def eligible(self, now, saved_reset=None):
        if now < self.observed or now - self.observed > 120:
            return False, "stale usage snapshot"
        if self.five.reset <= now or self.weekly.reset <= now:
            return False, "reset pending fresh server observation"
        if saved_reset is not None and (now < saved_reset or self.five.reset <= saved_reset):
            return False, "waiting for observed five-hour reset to advance"
        if self.weekly.used >= 100:
            return False, "weekly quota exhausted"
        if self.reached or self.five.used >= 100:
            return False, "five-hour quota exhausted"
        return True, "available"


def parse(payload, observed, now, bucket="codex"):
    try:
        if not math.isfinite(observed) or observed > now or now - observed > 120:
            raise ValueError("stale observation")
        data = payload.get("result", payload)
        buckets = data.get("rateLimitsByLimitId")
        selected = buckets.get(bucket) if buckets is not None else data.get("rateLimits")
        if not selected or selected.get("limitId") != bucket:
            raise ValueError("selected bucket missing")
        windows = {}
        for name in ("primary", "secondary"):
            item = selected.get(name)
            if item is None:
                continue
            duration, reset, used = (item.get(k) for k in ("windowDurationMins", "resetsAt", "usedPercent"))
            if type(duration) is not int or duration <= 0 or type(reset) is not int:
                raise ValueError("invalid duration or reset")
            if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100:
                raise ValueError("invalid usage")
            if reset <= now or reset > now + duration * 60 + 120 or duration in windows:
                raise ValueError("inconsistent reset/window")
            windows[duration] = Window(used, duration, reset)
        return Usage(windows[300], windows[10080], observed, bool(selected.get("rateLimitReachedType")))
    except (TypeError, KeyError, ValueError, AttributeError) as error:
        raise UsageError("missing, stale, or inconsistent usage data") from error
