from __future__ import annotations

import base64
from http import HTTPStatus

from aarkib.extensions import db
from aarkib.models import User
from aarkib.services.security import (
    AuthRateLimiter,
    is_private_or_local_ip,
)


def test_private_ip_detection():
    """Verifies that private and loopback IPs are recognized, and public/invalid IPs are rejected."""
    # Local & Loopback
    assert is_private_or_local_ip("127.0.0.1") is True
    assert is_private_or_local_ip("::1") is True
    assert is_private_or_local_ip("localhost") is False  # not an IP string

    # RFC 1918 Private Ranges
    assert is_private_or_local_ip("10.0.0.1") is True
    assert is_private_or_local_ip("10.254.0.1") is True
    assert is_private_or_local_ip("172.16.0.1") is True
    assert is_private_or_local_ip("172.31.255.255") is True
    assert is_private_or_local_ip("192.168.0.1") is True
    assert is_private_or_local_ip("192.168.1.100") is True

    # Public Internet IPs
    assert is_private_or_local_ip("8.8.8.8") is False
    assert is_private_or_local_ip("1.1.1.1") is False
    assert is_private_or_local_ip("203.0.113.195") is False
    assert (
        is_private_or_local_ip("172.32.0.1") is False
    )  # outside RFC 1918 172.16.0.0/12

    # Malformed / Empty
    assert is_private_or_local_ip("") is False
    assert is_private_or_local_ip(None) is False
    assert is_private_or_local_ip("not-an-ip") is False


def test_passwordless_user_lan_rules():
    """Verifies passwordless accounts require a local/private network connection unless overridden."""
    user = User(username="lan_reader")
    user.set_password(None)
    assert user.has_password is False

    # LAN / Loopback connections succeed with empty or omitted password
    assert user.check_password("", client_ip="127.0.0.1") is True
    assert user.check_password(None, client_ip="192.168.1.50") is True
    assert user.check_password("", client_ip="10.0.0.2") is True

    # Non-empty password still fails
    assert user.check_password("wrongpassword", client_ip="192.168.1.50") is False

    # Public / Remote IP connections are strictly rejected
    assert user.check_password("", client_ip="203.0.113.195") is False
    assert user.check_password(None, client_ip="8.8.8.8") is False

    # Explicit server override allows remote passwordless
    assert (
        user.check_password(
            "", client_ip="203.0.113.195", allow_remote_passwordless=True
        )
        is True
    )

    # Standard password-protected users are NOT subject to LAN gating
    protected_user = User(username="remote_user")
    protected_user.set_password("SecretPass123!")
    assert protected_user.check_password("SecretPass123!", client_ip="8.8.8.8") is True
    assert (
        protected_user.check_password("SecretPass123!", client_ip="192.168.1.50")
        is True
    )


def test_auth_rate_limiter_sliding_window():
    """Verifies sliding window rate limiter triggers and resets properly."""
    limiter = AuthRateLimiter(max_attempts=3, window_seconds=60)
    test_ip = "198.51.100.42"
    other_ip = "198.51.100.43"

    # Initially not limited
    limited, _ = limiter.is_rate_limited(test_ip)
    assert limited is False

    # 1st and 2nd failures
    limiter.record_failure(test_ip)
    limiter.record_failure(test_ip)
    limited, _ = limiter.is_rate_limited(test_ip)
    assert limited is False

    # 3rd failure reaches max_attempts
    limiter.record_failure(test_ip)
    limited, retry_after = limiter.is_rate_limited(test_ip)
    assert limited is True
    assert 1 <= retry_after <= 60

    # Other IP is unaffected
    limited_other, _ = limiter.is_rate_limited(other_ip)
    assert limited_other is False

    # Successful login resets failure counter
    limiter.reset(test_ip)
    limited, _ = limiter.is_rate_limited(test_ip)
    assert limited is False


def test_login_endpoint_rate_limiting(app, client):
    """Verifies POST /auth/login returns HTTP 429 after exceeding max attempts."""
    with app.app_context():
        # Ensure at least 1 user exists so login page doesn't redirect to setup
        user = User(username="rate_user")
        user.set_password("ValidPassword123")
        db.session.add(user)
        db.session.commit()

    attacker_ip = "198.51.100.99"

    # Temporarily enable rate limiting for this test
    app.config["AUTH_RATE_LIMIT_ENABLED"] = True
    app.config["AUTH_RATE_LIMIT_MAX_ATTEMPTS"] = 3
    app.config["AUTH_RATE_LIMIT_WINDOW_SECONDS"] = 30

    from aarkib.services.security import auth_rate_limiter

    auth_rate_limiter.reset(attacker_ip)

    try:
        # 3 failed attempts
        for _ in range(3):
            res = client.post(
                "/auth/login",
                data={"username": "rate_user", "password": "WrongPassword"},
                environ_overrides={"REMOTE_ADDR": attacker_ip},
            )
            assert res.status_code != HTTPStatus.TOO_MANY_REQUESTS

        # 4th attempt must be throttled with HTTP 429
        res_throttled = client.post(
            "/auth/login",
            data={"username": "rate_user", "password": "WrongPassword"},
            environ_overrides={"REMOTE_ADDR": attacker_ip},
        )
        assert res_throttled.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert b"Too many failed login attempts" in res_throttled.data

    finally:
        # Reset rate limiting to default test config
        app.config["AUTH_RATE_LIMIT_ENABLED"] = False
        auth_rate_limiter.reset(attacker_ip)


def test_passwordless_basic_auth_integration(app, client):
    """Verifies passwordless user can authenticate over Basic Auth on LAN but not from public IP."""
    with app.app_context():
        reader = User(username="kid_reader")
        reader.set_password(None)
        db.session.add(reader)
        db.session.commit()

    basic_auth_header = {
        "Authorization": f"Basic {base64.b64encode(b'kid_reader:').decode()}"
    }

    # 1. Access from local LAN IP -> HTTP 200 OK
    res_lan = app.test_client().get(
        "/api/media",
        headers=basic_auth_header,
        environ_overrides={"REMOTE_ADDR": "192.168.1.75"},
    )
    assert res_lan.status_code == HTTPStatus.OK

    # 2. Access from public WAN IP -> HTTP 401 Unauthorized
    res_wan = app.test_client().get(
        "/api/media",
        headers=basic_auth_header,
        environ_overrides={"REMOTE_ADDR": "203.0.113.88"},
    )
    assert res_wan.status_code == HTTPStatus.UNAUTHORIZED

    # 3. Access with allow_remote_passwordless enabled -> HTTP 200 OK
    app.config["ALLOW_PASSWORDLESS_REMOTE"] = True
    try:
        res_wan_allowed = app.test_client().get(
            "/api/media",
            headers=basic_auth_header,
            environ_overrides={"REMOTE_ADDR": "203.0.113.88"},
        )
        assert res_wan_allowed.status_code == HTTPStatus.OK
    finally:
        app.config["ALLOW_PASSWORDLESS_REMOTE"] = False
