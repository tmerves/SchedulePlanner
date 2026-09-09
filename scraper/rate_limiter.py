"""
Process-wide and distributed rate limiting module for external university registrar requests.

Ensures that concurrent Streamlit sessions and threads coordinate external HTTP traffic,
preventing target host overload, burst synchronization, and IP-level bans.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import email.utils
import logging
import math
import os
import random
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Configurable defaults via environment variables
DEFAULT_REQUESTS_PER_SECOND = float(os.getenv("SCRAPER_REQUESTS_PER_SECOND", "2.0"))
DEFAULT_BURST_CAPACITY = int(os.getenv("SCRAPER_BURST_CAPACITY", "2"))
DEFAULT_JITTER_MIN = float(os.getenv("SCRAPER_JITTER_MIN", "0.05"))
DEFAULT_JITTER_MAX = float(os.getenv("SCRAPER_JITTER_MAX", "0.2"))
DEFAULT_BASE_COOLDOWN = float(os.getenv("SCRAPER_BASE_COOLDOWN", "5.0"))
DEFAULT_MAX_COOLDOWN = float(os.getenv("SCRAPER_MAX_COOLDOWN", "60.0"))
DEFAULT_MAX_RETRIES = int(os.getenv("SCRAPER_MAX_RETRIES", "3"))


def parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """
    Parses an HTTP 'Retry-After' header value, which can be either a delay in seconds
    or an HTTP-date string (RFC 7231 / RFC 9110). Returns the delay in seconds, or None.
    """
    if not header_value:
        return None
    header_clean = str(header_value).strip()
    if not header_clean:
        return None

    # Try numeric seconds first
    try:
        seconds = float(header_clean)
        return max(0.0, seconds)
    except ValueError:
        pass

    # Try RFC 2822 / RFC 7231 date format
    try:
        target_dt = email.utils.parsedate_to_datetime(header_clean)
        now_dt = datetime.now(timezone.utc)
        diff = (target_dt - now_dt).total_seconds()
        return max(0.0, diff)
    except Exception:
        logger.debug("Failed to parse Retry-After header: %r", header_value)
        return None


class BaseRateLimiter(ABC):
    """Abstract interface for rate limiters coordinating external requests."""

    @abstractmethod
    def acquire(self) -> float:
        """
        Acquires permission to execute a request. Blocks the caller until allowed.
        Returns the total wait time in seconds.
        """
        raise NotImplementedError

    @abstractmethod
    def report_rate_limit_error(
        self, status_code: int = 429, retry_after: Optional[float] = None
    ) -> float:
        """
        Signals that a rate limit or service overload response (e.g. 429, 503) was received.
        Triggers exponential backoff and sets a cooldown window.
        Returns the active cooldown duration in seconds.
        """
        raise NotImplementedError

    @abstractmethod
    def report_success(self) -> None:
        """Signals that a request succeeded, resetting consecutive error tracking."""
        raise NotImplementedError

    @abstractmethod
    def is_in_cooldown(self) -> bool:
        """Checks if the limiter is currently in an active cooldown window."""
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        """Resets the internal limiter state."""
        raise NotImplementedError


class ProcessRateLimiter(BaseRateLimiter):
    """
    Thread-safe, process-wide rate limiter implementing a Token Bucket algorithm
    with reservations, randomized jitter, and exponential cooldown backoff.

    Coordinates all Streamlit sessions and threads within the same Python process.
    """

    def __init__(
        self,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        burst: int = DEFAULT_BURST_CAPACITY,
        jitter_range: Tuple[float, float] = (DEFAULT_JITTER_MIN, DEFAULT_JITTER_MAX),
        base_cooldown: float = DEFAULT_BASE_COOLDOWN,
        max_cooldown: float = DEFAULT_MAX_COOLDOWN,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be greater than 0")
        if burst < 1:
            raise ValueError("burst must be at least 1")

        self.requests_per_second = float(requests_per_second)
        self.burst = int(burst)
        self.jitter_min = float(jitter_range[0])
        self.jitter_max = float(jitter_range[1])
        self.base_cooldown = float(base_cooldown)
        self.max_cooldown = float(max_cooldown)
        self.max_retries = int(max_retries)

        self._lock = threading.Lock()
        self._tokens = float(self.burst)
        self._last_refill = time.monotonic()
        self._reserved_until = 0.0
        self._cooldown_until = 0.0
        self._consecutive_errors = 0

    def _reserve_slot(self) -> float:
        """Internal helper to reserve a request slot under lock."""
        with self._lock:
            now = time.monotonic()
            base_time = max(now, self._cooldown_until, self._reserved_until)

            # Refill tokens accumulated between last refill and base_time
            elapsed = base_time - self._last_refill
            if elapsed > 0:
                self._tokens = min(
                    float(self.burst),
                    self._tokens + elapsed * self.requests_per_second,
                )
                self._last_refill = base_time

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                scheduled_time = base_time
            else:
                deficit = 1.0 - self._tokens
                wait_needed = deficit / self.requests_per_second
                scheduled_time = base_time + wait_needed
                self._tokens = 0.0
                self._last_refill = scheduled_time

            jitter = (
                random.uniform(self.jitter_min, self.jitter_max)
                if self.jitter_max > 0
                else 0.0
            )
            self._reserved_until = scheduled_time
            wait_delay = max(0.0, scheduled_time - now) + jitter
            return wait_delay

    def acquire(self) -> float:
        """
        Blocks the calling thread until permitted to send a request.
        Handles token bucket pacing, jitter, and cooldown windows.
        """
        total_slept = 0.0
        while True:
            delay = self._reserve_slot()
            if delay > 0:
                time.sleep(delay)
                total_slept += delay

            # Re-check cooldown: if a 429/503 occurred while we were sleeping, re-reserve
            with self._lock:
                now = time.monotonic()
                if now < self._cooldown_until:
                    continue
                break

        return total_slept

    def report_rate_limit_error(
        self, status_code: int = 429, retry_after: Optional[float] = None
    ) -> float:
        """
        Triggers exponential backoff and enforces a process-wide cooldown window.
        """
        with self._lock:
            self._consecutive_errors += 1
            backoff = self.base_cooldown * (2 ** (self._consecutive_errors - 1))
            backoff = min(self.max_cooldown, backoff)

            if retry_after is not None and retry_after > backoff:
                backoff = min(self.max_cooldown, retry_after)

            jitter = random.uniform(0.1, 0.4)
            cooldown = backoff + jitter

            now = time.monotonic()
            self._cooldown_until = max(self._cooldown_until, now + cooldown)
            self._reserved_until = max(self._reserved_until, self._cooldown_until)
            self._tokens = 0.0
            self._last_refill = self._cooldown_until

            logger.warning(
                "ProcessRateLimiter: HTTP %d received. Cooldown active for %.2fs "
                "(errors=%d, cooldown_until=%.2f).",
                status_code,
                cooldown,
                self._consecutive_errors,
                self._cooldown_until,
            )
            return cooldown

    def report_success(self) -> None:
        """Resets consecutive error tracking on a successful response."""
        with self._lock:
            if self._consecutive_errors > 0:
                self._consecutive_errors = 0

    def is_in_cooldown(self) -> bool:
        """Returns True if the limiter is currently in a cooldown window."""
        with self._lock:
            return time.monotonic() < self._cooldown_until

    def reset(self) -> None:
        """Resets state (primarily for test isolation)."""
        with self._lock:
            self._tokens = float(self.burst)
            self._last_refill = time.monotonic()
            self._reserved_until = 0.0
            self._cooldown_until = 0.0
            self._consecutive_errors = 0



class RedisRateLimiter(BaseRateLimiter):
    """
    Distributed rate limiter coordinator using Redis for multi-worker or multi-replica
    deployments where Streamlit runs across separate OS processes or container instances.

    Uses Redis Lua script for token bucket atomicity and Redis keys with TTL
    for cluster-wide cooldown windows on HTTP 429/503.
    """

    def __init__(
        self,
        redis_client=None,
        key_prefix: str = "schedule_planner:rate_limiter",
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        burst: int = DEFAULT_BURST_CAPACITY,
        jitter_range: Tuple[float, float] = (DEFAULT_JITTER_MIN, DEFAULT_JITTER_MAX),
        base_cooldown: float = DEFAULT_BASE_COOLDOWN,
        max_cooldown: float = DEFAULT_MAX_COOLDOWN,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        if redis_client is None:
            try:
                import redis
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
                self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
            except ImportError as err:
                raise ImportError(
                    "The 'redis' package is required for RedisRateLimiter. "
                    "Install with `pip install redis`."
                ) from err
        else:
            self.redis = redis_client

        self.key_prefix = key_prefix
        self.requests_per_second = float(requests_per_second)
        self.burst = int(burst)
        self.jitter_min = float(jitter_range[0])
        self.jitter_max = float(jitter_range[1])
        self.base_cooldown = float(base_cooldown)
        self.max_cooldown = float(max_cooldown)
        self.max_retries = int(max_retries)

    def acquire(self) -> float:
        cooldown_key = f"{self.key_prefix}:cooldown"
        total_waited = 0.0
        while True:
            ttl = self.redis.ttl(cooldown_key)
            if ttl is not None and ttl > 0:
                time.sleep(ttl)
                total_waited += ttl
                continue
            break

        lua_token_bucket = """
        local key = KEYS[1]
        local burst = tonumber(ARGV[1])
        local rate = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])

        local data = redis.call('HMGET', key, 'tokens', 'last_refill')
        local tokens = tonumber(data[1])
        local last_refill = tonumber(data[2])

        if not tokens or not last_refill then
            tokens = burst
            last_refill = now
        else
            local elapsed = math.max(0, now - last_refill)
            tokens = math.min(burst, tokens + elapsed * rate)
            last_refill = now
        end

        local wait_time = 0
        if tokens >= 1 then
            tokens = tokens - 1
        else
            wait_time = (1 - tokens) / rate
            tokens = 0
            last_refill = now + wait_time
        end

        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
        redis.call('EXPIRE', key, math.ceil(burst / rate) + 60)
        return wait_time
        """
        now = time.time()
        wait_time = float(
            self.redis.eval(
                lua_token_bucket,
                1,
                f"{self.key_prefix}:tokens",
                self.burst,
                self.requests_per_second,
                now,
            )
        )
        jitter = (
            random.uniform(self.jitter_min, self.jitter_max)
            if self.jitter_max > 0
            else 0.0
        )
        total_delay = wait_time + jitter
        if total_delay > 0:
            time.sleep(total_delay)
            total_waited += total_delay
        return total_waited

    def report_rate_limit_error(
        self, status_code: int = 429, retry_after: Optional[float] = None
    ) -> float:
        err_key = f"{self.key_prefix}:consecutive_errors"
        consecutive = self.redis.incr(err_key)
        self.redis.expire(err_key, int(self.max_cooldown * 2))

        backoff = self.base_cooldown * (2 ** (consecutive - 1))
        backoff = min(self.max_cooldown, backoff)
        if retry_after is not None and retry_after > backoff:
            backoff = min(self.max_cooldown, retry_after)

        cooldown = backoff + random.uniform(0.1, 0.4)
        cooldown_key = f"{self.key_prefix}:cooldown"
        self.redis.set(cooldown_key, "1", ex=int(math.ceil(cooldown)))
        return cooldown

    def report_success(self) -> None:
        self.redis.delete(f"{self.key_prefix}:consecutive_errors")

    def is_in_cooldown(self) -> bool:
        ttl = self.redis.ttl(f"{self.key_prefix}:cooldown")
        return ttl is not None and ttl > 0

    def reset(self) -> None:
        self.redis.delete(
            f"{self.key_prefix}:tokens",
            f"{self.key_prefix}:cooldown",
            f"{self.key_prefix}:consecutive_errors",
        )


_GLOBAL_LIMITER_LOCK = threading.Lock()
_GLOBAL_RATE_LIMITER: Optional[ProcessRateLimiter] = None


def get_global_rate_limiter() -> ProcessRateLimiter:
    """Returns the shared process-wide rate limiter singleton."""
    global _GLOBAL_RATE_LIMITER
    if _GLOBAL_RATE_LIMITER is None:
        with _GLOBAL_LIMITER_LOCK:
            if _GLOBAL_RATE_LIMITER is None:
                _GLOBAL_RATE_LIMITER = ProcessRateLimiter()
    return _GLOBAL_RATE_LIMITER


def set_global_rate_limiter(limiter: Optional[ProcessRateLimiter]) -> None:
    """Allows setting or resetting the global rate limiter instance (useful for testing)."""
    global _GLOBAL_RATE_LIMITER
    with _GLOBAL_LIMITER_LOCK:
        _GLOBAL_RATE_LIMITER = limiter

