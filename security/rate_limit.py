from __future__ import annotations

import threading
import time

from config.settings import settings


class SlidingLockoutLimiter:
    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 900,
        base_lockout_seconds: int = 900,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(10, int(window_seconds))
        self.base_lockout_seconds = max(10, int(base_lockout_seconds))
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._strikes: dict[str, int] = {}
        self._last_gc = time.monotonic()

    def _gc_locked(self, now: float) -> None:
        if (now - self._last_gc) < 60.0 and len(self._events) < 1024:
            return
        self._last_gc = now
        for k, exp in list(self._locked_until.items()):
            if exp <= now:
                self._locked_until.pop(k, None)
        for k, hist in list(self._events.items()):
            fresh = [t for t in hist if (now - t) < self.window_seconds]
            if fresh:
                self._events[k] = fresh
            elif k not in self._locked_until:
                self._events.pop(k, None)
                self._strikes.pop(k, None)

    def check(self, key: str) -> tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            self._gc_locked(now)
            exp = self._locked_until.get(key)
            if exp is not None:
                rem = int(exp - now)
                if rem > 0:
                    return True, rem
                self._locked_until.pop(key, None)
            return False, 0

    def hit(self, key: str) -> tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            self._gc_locked(now)
            exp = self._locked_until.get(key)
            if exp is not None and exp > now:
                return True, int(exp - now)

            hist = [t for t in self._events.get(key, []) if (now - t) < self.window_seconds]
            hist.append(now)
            self._events[key] = hist
            if len(hist) >= self.max_attempts:
                strike = min(4, self._strikes.get(key, 0) + 1)
                self._strikes[key] = strike
                lock_dur = self.base_lockout_seconds * (2 ** (strike - 1))
                self._locked_until[key] = now + lock_dur
                return True, int(lock_dur)
            return False, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
            self._locked_until.pop(key, None)
            self._strikes.pop(key, None)


login_ip_limiter = SlidingLockoutLimiter(
    max_attempts=settings.AUTH_MAX_LOGIN_ATTEMPTS,
    window_seconds=900,
    base_lockout_seconds=settings.AUTH_LOCKOUT_SECONDS,
)
step_up_limiter = SlidingLockoutLimiter(
    max_attempts=5,
    window_seconds=600,
    base_lockout_seconds=300,
)
mutation_api_limiter = SlidingLockoutLimiter(
    max_attempts=45,
    window_seconds=60,
    base_lockout_seconds=60,
)
bi_api_limiter = SlidingLockoutLimiter(
    max_attempts=180,
    window_seconds=60,
    base_lockout_seconds=30,
)
