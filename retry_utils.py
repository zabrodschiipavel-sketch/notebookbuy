"""Small retry helper for network / API calls."""
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TypeVar


log = logging.getLogger(__name__)

T = TypeVar("T")

_RATE_LIMIT_HINTS = ("429", "resource exhausted", "quota", "rate limit", "too many requests")

# Google's genai client stringifies the API error body, which carries the
# server's own back-off hint: {'@type': '...RetryInfo', 'retryDelay': '16s'}.
_RETRY_DELAY_RE = re.compile(
    r"""['"]?retryDelay['"]?\s*:\s*['"]?(\d+(?:\.\d+)?)s['"]?""",
    re.IGNORECASE,
)


class RateLimiter:
    """Thread-safe sliding-window limiter: at most *max_per_window* calls per window.

    Gemini's free tier enforces a per-model requests-per-minute quota. Without a
    limiter the thread pool burns the whole minute's budget in a few seconds and
    every subsequent call 429s, so the limiter — not the retry loop — is what
    keeps the pipeline inside quota.
    """

    def __init__(self, max_per_window: int, window_sec: float = 60.0):
        self.max_per_window = max(1, max_per_window)
        self.window_sec = window_sec
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()

    def acquire(self) -> None:
        """Block until a slot frees up, then record this call."""
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - self.window_sec
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()

                if len(self._timestamps) < self.max_per_window:
                    self._timestamps.append(now)
                    return

                wait = self._timestamps[0] + self.window_sec - now

            # Sleep outside the lock so other threads can drain the window.
            time.sleep(max(wait, 0.001))


def parse_retry_delay(exc: BaseException) -> float | None:
    """Return the server-suggested back-off in seconds, or None when absent."""
    match = _RETRY_DELAY_RE.search(str(exc))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay_sec: float = 1.0,
    label: str = "request",
    max_delay_sec: float = 60.0,
    limiter: RateLimiter | None = None,
) -> T:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if limiter is not None:
            limiter.acquire()
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries - 1:
                break
            msg = str(exc).lower()
            delay = base_delay_sec * (2**attempt)
            if any(hint in msg for hint in _RATE_LIMIT_HINTS):
                delay *= 2
            suggested = parse_retry_delay(exc)
            if suggested is not None:
                # The server knows when the window reopens; guessing shorter
                # only wastes an attempt on a guaranteed 429.
                delay = max(delay, suggested + 0.5)
            delay = min(delay, max_delay_sec)
            log.warning("%s failed (attempt %s/%s): %s; retry in %.1fs", label, attempt + 1, max_retries, exc, delay)
            time.sleep(delay)
    assert last_error is not None
    raise last_error
