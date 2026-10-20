"""Bounded, single-process authentication attempt accounting; no HTTP or users."""

from collections import deque
import math
from threading import Lock
from time import monotonic
from typing import Callable


class AuthenticationRateLimitError(ValueError):
    def __init__(self, retry_after_seconds: int):
        super().__init__("AUTH_RATE_LIMITED")
        self.code = "AUTH_RATE_LIMITED"
        self.retry_after_seconds = retry_after_seconds


class AuthenticationRateLimiter:
    """Count admitted attempts, including successes; denials do not extend windows."""

    def __init__(self, now_provider: Callable[[], float] | None = None, *, max_selectors: int = 256):
        if type(max_selectors) is not int or max_selectors <= 0:
            raise ValueError("INVALID_RATE_LIMIT_CAPACITY")
        self._now = now_provider or monotonic
        self._max_selectors = max_selectors
        self._global: deque[float] = deque()
        self._selectors: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check_and_record(self, selector: str) -> None:
        if not isinstance(selector, str) or not 1 <= len(selector) <= 64:
            raise ValueError("INVALID_SELECTOR")
        with self._lock:
            now = self._now()
            if not math.isfinite(now):
                raise ValueError("INVALID_RATE_LIMIT_CLOCK")
            while self._global and self._global[0] <= now - 60:
                self._global.popleft()
            for key, attempts in list(self._selectors.items()):
                while attempts and attempts[0] <= now - 300:
                    attempts.popleft()
                if not attempts:
                    del self._selectors[key]
            attempts = self._selectors.get(selector)
            waits = []
            if len(self._global) >= 20:
                waits.append(self._global[0] + 60 - now)
            if attempts is not None and len(attempts) >= 5:
                waits.append(attempts[0] + 300 - now)
            if attempts is None and len(self._selectors) >= self._max_selectors:
                waits.append(min(values[-1] + 300 - now for values in self._selectors.values()))
            if waits:
                raise AuthenticationRateLimitError(max(1, math.ceil(max(waits))))
            self._global.append(now)
            self._selectors.setdefault(selector, deque()).append(now)
