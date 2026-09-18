from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections import defaultdict

from flask import current_app, request

logger = logging.getLogger(__name__)


def get_client_ip() -> str:
    """Extracts client IP from standard proxy headers or socket address."""
    if request:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # First IP in list is original client
            client_ip = forwarded.split(",")[0].strip()
            if client_ip:
                return client_ip

        real_ip = request.headers.get("X-Real-IP")
        if real_ip and real_ip.strip():
            return real_ip.strip()

        if request.remote_addr:
            return request.remote_addr.strip()

    return "127.0.0.1"


_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def is_private_or_local_ip(ip_str: str | None) -> bool:
    """Validates if an IP belongs strictly to private network ranges (RFC 1918) or loopback."""
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        return any(ip in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


class AuthRateLimiter:
    """Thread-safe sliding-window rate limiter for authentication endpoints."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_rate_limited(self, ip: str) -> tuple[bool, int]:
        """Checks if client IP has exceeded allowed failed authentication attempts.

        Returns:
            (is_limited, retry_after_seconds)
        """
        # Allow disabling rate limiting (e.g. in test suite)
        if current_app:
            if not current_app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
                return False, 0
            max_attempts = current_app.config.get(
                "AUTH_RATE_LIMIT_MAX_ATTEMPTS", self.max_attempts
            )
            window_seconds = current_app.config.get(
                "AUTH_RATE_LIMIT_WINDOW_SECONDS", self.window_seconds
            )
        else:
            max_attempts = self.max_attempts
            window_seconds = self.window_seconds

        now = time.time()
        with self._lock:
            # Clean expired timestamps
            timestamps = [t for t in self._attempts[ip] if now - t < window_seconds]
            self._attempts[ip] = timestamps

            if len(timestamps) >= max_attempts:
                oldest = timestamps[0]
                retry_after = max(1, int(window_seconds - (now - oldest)))
                logger.warning(
                    "Auth rate limit exceeded for IP %s (failed %d times, retry in %ds)",
                    ip,
                    len(timestamps),
                    retry_after,
                )
                return True, retry_after

            return False, 0

    def record_failure(self, ip: str, username: str | None = None) -> None:
        """Records a failed authentication attempt for the given IP."""
        now = time.time()
        should_emit_lockout = False
        attempts_count = 0
        with self._lock:
            self._attempts[ip].append(now)
            attempts_count = len(self._attempts[ip])
            if current_app:
                if not current_app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
                    return
                max_attempts = current_app.config.get(
                    "AUTH_RATE_LIMIT_MAX_ATTEMPTS", self.max_attempts
                )
            else:
                max_attempts = self.max_attempts
            if attempts_count == max_attempts:
                should_emit_lockout = True

        if should_emit_lockout:
            try:
                from aarkib.services.events import EVENT_SECURITY_LOCKOUT, event_bus

                event_bus.emit(
                    EVENT_SECURITY_LOCKOUT,
                    {
                        "ip": ip,
                        "attempts": attempts_count,
                        "username": username,
                        "timestamp": now,
                    },
                )
            except Exception:
                pass

    def reset(self, ip: str) -> None:
        """Clears failed attempts for an IP upon successful authentication."""
        with self._lock:
            self._attempts.pop(ip, None)


# Global singleton instance
auth_rate_limiter = AuthRateLimiter()
