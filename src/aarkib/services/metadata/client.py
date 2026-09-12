from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from aarkib.services.metadata.cache import MetadataCacheManager
from aarkib.services.metadata.limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Aarkib/0.1.0 (https://github.com/syfq91/aarkib; contact: support@aarkib.local)"
)
DEFAULT_TIMEOUT = 10


def is_safe_http_url(url: str) -> bool:
    """Verifies that a URL strictly uses http or https schemes."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


class ResilientHttpClient:
    """HTTP client with URL validation, rate limiting, caching, and retry backoff."""

    def __init__(
        self,
        provider_name: str,
        limiter: TokenBucketRateLimiter | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        max_retries: int = 3,
        initial_backoff: float = 1.0,
        backoff_factor: float = 2.0,
        jitter: float = 0.5,
    ) -> None:
        self.provider_name = provider_name
        self.limiter = limiter
        self.user_agent = user_agent
        self.max_retries = max(0, max_retries)
        self.initial_backoff = initial_backoff
        self.backoff_factor = backoff_factor
        self.jitter = jitter

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        ttl_days: int | None = None,
    ) -> Any | None:
        """Performs a GET request expecting a JSON payload, with caching and retries."""
        if not is_safe_http_url(url):
            logger.debug("Rejected unsafe URL: %s", url)
            return None

        # Assemble full URL if query params provided
        full_url = url
        if params:
            query_str = urllib.parse.urlencode(params)
            sep = "&" if "?" in url else "?"
            full_url = f"{url}{sep}{query_str}"

        # 1. Consult local SQLite cache
        if use_cache:
            cached = MetadataCacheManager.get(self.provider_name, url, params)
            if cached is not None:
                return cached

        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        if not full_url.startswith(("http://", "https://")):
            logger.warning("Rejected non-HTTP(S) metadata request URL: %s", full_url)
            return None

        attempt = 0
        while attempt <= self.max_retries:
            # 2. Rate limiter throttling
            if self.limiter:
                self.limiter.acquire(1.0)

            try:
                req = urllib.request.Request(full_url, headers=req_headers)
                with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                    if resp.status == 200:
                        raw = resp.read().decode("utf-8")
                        data = json.loads(raw)
                        # 3. Store in cache
                        if use_cache:
                            MetadataCacheManager.set(
                                self.provider_name,
                                url,
                                params,
                                data,
                                ttl_days=ttl_days,
                            )
                        return data
                    elif resp.status in (429, 503, 504):
                        # Transient retryable status
                        retry_after = resp.headers.get("Retry-After")
                        delay = self._compute_delay(attempt, retry_after)
                        logger.warning(
                            "HTTP %d from %s (attempt %d/%d), sleeping %.2fs",
                            resp.status,
                            self.provider_name,
                            attempt + 1,
                            self.max_retries,
                            delay,
                        )
                        time.sleep(delay)
                        attempt += 1
                        continue
                    else:
                        logger.debug(
                            "HTTP %d for %s: %s",
                            resp.status,
                            self.provider_name,
                            full_url,
                        )
                        return None

            except urllib.error.HTTPError as err:
                if err.code in (429, 503, 504) and attempt < self.max_retries:
                    retry_after = err.headers.get("Retry-After")
                    delay = self._compute_delay(attempt, retry_after)
                    logger.warning(
                        "HTTP %d from %s (attempt %d/%d), sleeping %.2fs",
                        err.code,
                        self.provider_name,
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                else:
                    logger.debug("HTTPError %s for %s", err, full_url)
                    return None
            except (urllib.error.URLError, TimeoutError) as err:
                if attempt < self.max_retries:
                    delay = self._compute_delay(attempt, None)
                    logger.warning(
                        "Network error from %s (%s, attempt %d/%d), sleeping %.2fs",
                        self.provider_name,
                        err,
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                else:
                    logger.debug("URLError %s for %s", err, full_url)
                    return None
            except Exception as exc:
                logger.debug("Unexpected error during HTTP GET %s: %s", full_url, exc)
                return None

        return None

    def get_bytes(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> bytes | None:
        """Downloads binary bytes (e.g. cover art images) safely."""
        if not is_safe_http_url(url):
            return None

        if self.limiter:
            self.limiter.acquire(1.0)

        if not url.startswith(("http://", "https://")):
            logger.warning("Rejected non-HTTP(S) download URL: %s", url)
            return None

        req_headers = {"User-Agent": self.user_agent}
        if headers:
            req_headers.update(headers)

        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception as exc:
            logger.debug("Failed to download bytes from %s: %s", url, exc)
        return None

    def _compute_delay(self, attempt: int, retry_after: str | None) -> float:
        """Calculates backoff delay taking Retry-After header into account."""
        if retry_after:
            try:
                val = float(retry_after)
                if 0 < val <= 60:
                    return val
            except ValueError, TypeError:
                pass

        base = self.initial_backoff * (self.backoff_factor**attempt)
        rand_jitter = random.uniform(0, self.jitter)
        return min(30.0, base + rand_jitter)
