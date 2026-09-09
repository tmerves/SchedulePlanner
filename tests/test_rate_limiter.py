import os
import threading
import time
from datetime import datetime, timedelta, timezone
import email.utils
from unittest.mock import MagicMock
import httpx
import pytest

from scraper.rate_limiter import (
    ProcessRateLimiter,
    RedisRateLimiter,
    parse_retry_after,
    get_global_rate_limiter,
    set_global_rate_limiter,
)
from scraper.client import ScraperClient


@pytest.fixture
def search_results_html():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "searchResults.html")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return f.read()



def test_parse_retry_after_numeric():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("   ") is None
    assert parse_retry_after("10") == 10.0
    assert parse_retry_after("2.5") == 2.5
    assert parse_retry_after("-5") == 0.0


def test_parse_retry_after_http_date():
    future_time = datetime.now(timezone.utc) + timedelta(seconds=45)
    date_str = email.utils.format_datetime(future_time)
    parsed = parse_retry_after(date_str)
    assert parsed is not None
    assert 40.0 <= parsed <= 46.0

    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    past_date_str = email.utils.format_datetime(past_time)
    assert parse_retry_after(past_date_str) == 0.0

    assert parse_retry_after("invalid-date-string-xyz") is None


def test_token_bucket_burst_and_rate():
    # 10 req/s, burst=2, jitter=0
    limiter = ProcessRateLimiter(
        requests_per_second=10.0,
        burst=2,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.1,
    )

    # First 2 requests should be instantaneous (burst tokens)
    start = time.monotonic()
    w1 = limiter.acquire()
    w2 = limiter.acquire()
    assert w1 == 0.0
    assert w2 == 0.0
    assert time.monotonic() - start < 0.05

    # 3rd request should wait ~0.1s (1 / 10 RPS)
    w3 = limiter.acquire()
    elapsed = time.monotonic() - start
    assert 0.08 <= elapsed <= 0.15
    assert 0.08 <= w3 <= 0.15


def test_jitter_application():
    limiter = ProcessRateLimiter(
        requests_per_second=100.0,
        burst=1,
        jitter_range=(0.03, 0.06),
    )
    start = time.monotonic()
    wait = limiter.acquire()
    elapsed = time.monotonic() - start
    assert 0.025 <= elapsed <= 0.09
    assert 0.025 <= wait <= 0.09



def test_concurrent_sessions_rate_limiting():
    """
    Simulate multiple concurrent Streamlit session threads hitting the same limiter.
    Ensures that concurrent callers coordinate and requests do not exceed the rate.
    """
    # 20 RPS, burst=1, zero jitter
    limiter = ProcessRateLimiter(
        requests_per_second=20.0,
        burst=1,
        jitter_range=(0.0, 0.0),
    )
    timestamps = []
    lock = threading.Lock()

    def worker():
        limiter.acquire()
        with lock:
            timestamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_time = time.monotonic() - start
    assert len(timestamps) == 5
    # 5 requests with burst=1 at 20 RPS takes 4 intervals of 0.05s = ~0.20s
    assert total_time >= 0.18


def test_cooldown_exponential_backoff_and_reset():
    limiter = ProcessRateLimiter(
        requests_per_second=50.0,
        burst=2,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.2,
        max_cooldown=5.0,
    )
    assert not limiter.is_in_cooldown()

    # First error (attempt 1) -> 0.2 * 2^0 = 0.2s (+ jitter)
    c1 = limiter.report_rate_limit_error(status_code=429)
    assert limiter.is_in_cooldown()
    assert 0.2 <= c1 <= 0.7

    # Calling acquire during cooldown must block
    t0 = time.monotonic()
    limiter.acquire()
    dt = time.monotonic() - t0
    assert dt >= 0.18
    assert not limiter.is_in_cooldown()

    # Second error (attempt 2) -> 0.2 * 2^1 = 0.4s (+ jitter)
    c2 = limiter.report_rate_limit_error(status_code=503)
    assert 0.4 <= c2 <= 0.9

    # Success should reset consecutive errors
    limiter.report_success()
    # Next error should be back to base cooldown 0.2s
    c3 = limiter.report_rate_limit_error(status_code=429)
    assert 0.2 <= c3 <= 0.7


def test_cooldown_respects_retry_after():
    limiter = ProcessRateLimiter(
        requests_per_second=50.0,
        burst=2,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.1,
    )
    # retry_after of 0.5s is larger than base 0.1s
    c = limiter.report_rate_limit_error(status_code=429, retry_after=0.5)
    assert c >= 0.5




def test_client_handles_429_retry(search_results_html):
    """
    Test that ScraperClient retries when receiving HTTP 429 and succeeds once server responds 200.
    """
    limiter = ProcessRateLimiter(
        requests_per_second=100.0,
        burst=1,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.05,
        max_retries=2,
    )
    client = ScraperClient(term="0009", rate_limiter=limiter)

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0.05"}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.text = search_results_html
    resp_200.raise_for_status = MagicMock()

    # First call returns 429, second returns 200
    client.client.post = MagicMock(side_effect=[resp_429, resp_200])

    courses = client.fetch_courses("BACC")
    assert len(courses) > 0
    assert client.client.post.call_count == 2
    assert not limiter.is_in_cooldown()


def test_client_exhausts_retries_on_503():
    """
    Test that ScraperClient raises an HTTP error after max_retries on 503 Service Unavailable.
    """
    limiter = ProcessRateLimiter(
        requests_per_second=100.0,
        burst=1,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.02,
        max_retries=2,
    )
    client = ScraperClient(term="0009", rate_limiter=limiter)

    resp_503 = MagicMock()
    resp_503.status_code = 503
    resp_503.headers = {}
    resp_503.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("503 Service Unavailable", request=MagicMock(), response=resp_503)
    )

    client.client.post = MagicMock(return_value=resp_503)

    with pytest.raises(httpx.HTTPStatusError, match="503 Service Unavailable"):
        client.fetch_courses("BACC")

    # Initial attempt + 2 retries = 3 calls total
    assert client.client.post.call_count == 3


def test_redis_rate_limiter_mocked():
    """
    Verify RedisRateLimiter operates correctly with a mocked Redis client.
    """
    mock_redis = MagicMock()
    # Mock ttl: not in cooldown
    mock_redis.ttl = MagicMock(return_value=-1)
    # Mock eval: returns 0 wait time from Lua script
    mock_redis.eval = MagicMock(return_value=0.0)

    redis_limiter = RedisRateLimiter(
        redis_client=mock_redis,
        requests_per_second=10.0,
        burst=2,
        jitter_range=(0.0, 0.0),
        base_cooldown=1.0,
    )

    wait = redis_limiter.acquire()
    assert wait == 0.0
    mock_redis.eval.assert_called_once()

    # Report 429
    mock_redis.incr = MagicMock(return_value=1)
    c = redis_limiter.report_rate_limit_error(status_code=429)
    assert c >= 1.0
    mock_redis.set.assert_called_once()

    # Report success
    redis_limiter.report_success()
    mock_redis.delete.assert_called_with("schedule_planner:rate_limiter:consecutive_errors")




def test_process_rate_limiter_validation():
    with pytest.raises(ValueError, match="requests_per_second must be greater than 0"):
        ProcessRateLimiter(requests_per_second=0)
    with pytest.raises(ValueError, match="requests_per_second must be greater than 0"):
        ProcessRateLimiter(requests_per_second=-1.0)
    with pytest.raises(ValueError, match="burst must be at least 1"):
        ProcessRateLimiter(burst=0)


def test_scraper_clients_share_global_limiter_by_default():
    c1 = ScraperClient(term="0009")
    c2 = ScraperClient(term="0007")
    assert c1.rate_limiter is c2.rate_limiter
    assert c1.rate_limiter is get_global_rate_limiter()


def test_client_fetch_search_page_handles_429_retry():
    limiter = ProcessRateLimiter(
        requests_per_second=100.0,
        burst=1,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.05,
        max_retries=2,
    )
    client = ScraperClient(term="0009", rate_limiter=limiter)

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0.05"}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.text = "<html>Search Page</html>"
    resp_200.raise_for_status = MagicMock()

    client.client.get = MagicMock(side_effect=[resp_429, resp_200])

    html = client.fetch_search_page()
    assert html == "<html>Search Page</html>"
    assert client.client.get.call_count == 2


def test_redis_rate_limiter_missing_package():
    # When redis package is not installed and redis_client is None
    import sys
    orig_redis = sys.modules.get("redis")
    try:
        sys.modules["redis"] = None  # simulate missing package
        with pytest.raises(ImportError, match="The 'redis' package is required"):
            RedisRateLimiter(redis_client=None)
    finally:
        if orig_redis is not None:
            sys.modules["redis"] = orig_redis
        else:
            sys.modules.pop("redis", None)




def test_cooldown_cap_at_max_cooldown():
    limiter = ProcessRateLimiter(
        requests_per_second=50.0,
        burst=2,
        jitter_range=(0.0, 0.0),
        base_cooldown=1.0,
        max_cooldown=3.0,
    )
    # Huge retry_after is capped at max_cooldown (3.0) + jitter (0.1..0.4)
    c = limiter.report_rate_limit_error(status_code=429, retry_after=100.0)
    assert 3.0 <= c <= 3.5

    # Triggering repeated errors eventually escalates past max_cooldown (3.0) and stays capped
    for _ in range(5):
        c_repeat = limiter.report_rate_limit_error(status_code=503)
    assert 3.0 <= c_repeat <= 3.5


def test_process_rate_limiter_reset():
    limiter = ProcessRateLimiter(
        requests_per_second=1.0,
        burst=2,
        jitter_range=(0.0, 0.0),
        base_cooldown=1.0,
    )
    # Consume burst
    limiter.acquire()
    limiter.acquire()
    assert limiter._tokens < 0.1

    # Put into cooldown
    limiter.report_rate_limit_error(status_code=429)
    assert limiter.is_in_cooldown()

    # Reset
    limiter.reset()
    assert not limiter.is_in_cooldown()
    assert limiter._tokens == 2.0
    assert limiter._consecutive_errors == 0


def test_acquire_rechecks_cooldown_if_error_occurs():
    """
    Test the race condition where a thread is waiting to acquire and an error occurs,
    extending the cooldown. The acquiring thread must wait for the extended cooldown.
    """
    limiter = ProcessRateLimiter(
        requests_per_second=2.0,
        burst=1,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.2,
    )
    # Consume token so next request needs to wait
    limiter.acquire()

    waited = []

    def background_worker():
        t0 = time.monotonic()
        limiter.acquire()
        waited.append(time.monotonic() - t0)

    t = threading.Thread(target=background_worker)
    t.start()

    # While worker is queued/sleeping, trigger an error
    time.sleep(0.05)
    limiter.report_rate_limit_error(status_code=429)

    t.join()
    assert len(waited) == 1
    # Should have waited at least ~0.2s for cooldown
    assert waited[0] >= 0.18


def test_client_non_retriable_error():
    """
    Ensure non-429/503 errors (e.g. 404 or 500) do NOT trigger rate limiter cooldown
    and are raised immediately without retry.
    """
    limiter = ProcessRateLimiter(
        requests_per_second=50.0,
        burst=1,
        jitter_range=(0.0, 0.0),
        base_cooldown=0.1,
    )
    client = ScraperClient(term="0009", rate_limiter=limiter)

    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=resp_404)
    )

    client.client.post = MagicMock(return_value=resp_404)

    # 404 should not retry, should not trigger cooldown
    with pytest.raises(httpx.HTTPStatusError, match="404 Not Found"):
        client.fetch_courses("BACC")

    assert client.client.post.call_count == 1
    assert not limiter.is_in_cooldown()


def test_redis_rate_limiter_cooldown_blocking_and_reset():
    """
    Verify RedisRateLimiter waits when ttl > 0 and deletes all keys on reset.
    """
    mock_redis = MagicMock()
    # First ttl check returns 0.05, second returns -1
    mock_redis.ttl = MagicMock(side_effect=[0.05, -1])
    mock_redis.eval = MagicMock(return_value=0.0)

    redis_limiter = RedisRateLimiter(
        redis_client=mock_redis,
        requests_per_second=10.0,
        burst=2,
        jitter_range=(0.0, 0.0),
    )

    t0 = time.monotonic()
    wait = redis_limiter.acquire()
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.04
    assert wait >= 0.04
    assert mock_redis.ttl.call_count == 2
    mock_redis.eval.assert_called_once()

    # Test reset
    redis_limiter.reset()
    mock_redis.delete.assert_called_with(
        "schedule_planner:rate_limiter:tokens",
        "schedule_planner:rate_limiter:cooldown",
        "schedule_planner:rate_limiter:consecutive_errors",
    )

