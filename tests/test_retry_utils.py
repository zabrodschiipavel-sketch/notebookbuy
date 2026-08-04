"""Tests for the retry/back-off helper and the shared rate limiter."""
import threading
import time

import retry_utils
from retry_utils import RateLimiter, call_with_retry, parse_retry_delay


def test_parse_retry_delay_single_quoted():
    exc = Exception("... {'@type': '...RetryInfo', 'retryDelay': '16s'}}")
    assert parse_retry_delay(exc) == 16.0


def test_parse_retry_delay_double_quoted_fractional():
    exc = Exception('{"retryDelay": "16.36s"}')
    assert parse_retry_delay(exc) == 16.36


def test_parse_retry_delay_bare():
    assert parse_retry_delay(Exception("retryDelay: 5s")) == 5.0


def test_parse_retry_delay_absent():
    assert parse_retry_delay(Exception("503 Service Unavailable")) is None


def test_rate_limiter_blocks_once_window_is_full():
    limiter = RateLimiter(2, window_sec=0.3)
    start = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # must wait for the first slot to expire
    assert time.monotonic() - start >= 0.25


def test_rate_limiter_holds_under_concurrency():
    limiter = RateLimiter(3, window_sec=0.2)
    stamps: list[float] = []
    lock = threading.Lock()

    def worker():
        for _ in range(2):
            limiter.acquire()
            with lock:
                stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(stamps) == 8
    stamps.sort()
    # The limiter keeps entries newer than (now - window), so any half-open
    # window of that width may hold at most max_per_window acquires.
    for start in stamps:
        in_window = [s for s in stamps if start <= s < start + 0.2]
        assert len(in_window) <= 3, f"{len(in_window)} acquires inside one window"


def test_call_with_retry_honours_server_suggested_delay(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(retry_utils.time, "sleep", slept.append)
    attempts = 0

    def fn():
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RuntimeError("429 RESOURCE_EXHAUSTED {'retryDelay': '7s'}")
        return "ok"

    assert call_with_retry(fn, max_retries=3, base_delay_sec=0.5) == "ok"
    assert len(slept) == 2
    assert all(s >= 7.0 for s in slept)


def test_call_with_retry_clamps_to_max_delay(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(retry_utils.time, "sleep", slept.append)

    def fn():
        raise RuntimeError("429 quota {'retryDelay': '100s'}")

    try:
        call_with_retry(fn, max_retries=3, max_delay_sec=10.0)
    except RuntimeError:
        pass
    assert slept == [10.0, 10.0]


def test_call_with_retry_acquires_limiter_per_attempt(monkeypatch):
    calls: list[int] = []
    limiter = RateLimiter(10, window_sec=1.0)
    monkeypatch.setattr(retry_utils.time, "sleep", lambda _: None)
    monkeypatch.setattr(limiter, "acquire", lambda: calls.append(1))
    attempts = 0

    def fn():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("boom")
        return "ok"

    assert call_with_retry(fn, max_retries=3, limiter=limiter) == "ok"
    assert len(calls) == 3
