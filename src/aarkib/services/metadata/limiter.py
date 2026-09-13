from __future__ import annotations

import threading
import time


class TokenBucketRateLimiter:
    """Thread-safe token bucket rate limiter to enforce API request quotas.

    Ensures compliance with third-party service policies (e.g., MusicBrainz's strict
    1 request per second rule or TMDB burst limits).
    """

    def __init__(self, rate: float = 1.0, capacity: float = 1.0) -> None:
        """
        Args:
            rate: Token refill rate in tokens per second (e.g. 1.0 = 1 request/sec).
            capacity: Maximum burst token capacity.
        """
        self.rate = max(0.01, float(rate))
        self.capacity = max(1.0, float(capacity))
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens_needed: float = 1.0) -> float:
        """Blocks until the required tokens are available.

        Returns the number of seconds spent waiting.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now

            # Replenish tokens
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

            if self.tokens >= tokens_needed:
                self.tokens -= tokens_needed
                return 0.0

            # Compute sleep time needed to reach required tokens
            needed = tokens_needed - self.tokens
            sleep_time = needed / self.rate
            self.tokens = 0.0
            # Account for the time that will be spent sleeping
            self.last_update = now + sleep_time

        if sleep_time > 0:
            time.sleep(sleep_time)
        return sleep_time


# Pre-configured global rate limiters
# MusicBrainz: strict 1 request per second max
musicbrainz_limiter = TokenBucketRateLimiter(rate=1.0, capacity=1.0)

# TMDB: 4 requests per second, capacity of 40 tokens (burst friendly)
tmdb_limiter = TokenBucketRateLimiter(rate=4.0, capacity=40.0)

# Google Books / Open Library: 5 requests per second, capacity 20
books_limiter = TokenBucketRateLimiter(rate=5.0, capacity=20.0)

# ComicVine: 1 request per second
comicvine_limiter = TokenBucketRateLimiter(rate=1.0, capacity=2.0)
