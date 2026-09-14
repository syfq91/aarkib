"""Tests for API & Device Tokens authentication and management."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from flask import Flask

from aarkib.extensions import db
from aarkib.models import DeviceToken, MediaItem, User


@pytest.fixture
def app_with_tokens(tmp_path: Path) -> Flask:
    """Create a test Flask app with an admin, normal user, and sample book."""
    from aarkib import create_app
    from aarkib.config import TestConfig

    class TokenTestConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'tokens.db'}"
        DATA_DIR = tmp_path
        TESTING = True

    app = create_app(TokenTestConfig)
    with app.app_context():
        db.create_all()

        admin = User(username="admin", is_admin=True)
        admin.set_password("adminpass")
        user = User(username="reader", is_admin=False)
        user.set_password("readerpass")
        book = MediaItem(
            title="Dune",
            original_file_path=str(tmp_path / "dune.epub"),
            file_format="epub",
            file_hash="dunehash123",
        )
        db.session.add_all([admin, user, book])
        db.session.commit()

    return app


def test_device_token_generation_and_hashing(app_with_tokens: Flask) -> None:
    """Verify create_token generates ark_ secret, prefix, and valid SHA-256 hash."""
    with app_with_tokens.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "reader"))
        assert user is not None

        token_obj, raw_token = DeviceToken.create_token(
            user_id=user.id,
            name="KOReader E-Ink",
            scopes=["read", "stream"],
            expires_in_days=30,
        )
        assert raw_token.startswith("ark_")
        assert len(raw_token) > 40
        assert token_obj.token_prefix == raw_token[:10] + "..."
        assert token_obj.token_hash == DeviceToken.hash_token(raw_token)
        assert token_obj.scopes == ["read", "stream"]
        assert token_obj.expires_at is not None

        db.session.add(token_obj)
        db.session.commit()

        # Reload from DB
        loaded = db.session.get(DeviceToken, token_obj.id)
        assert loaded is not None
        assert loaded.name == "KOReader E-Ink"
        assert loaded.user.username == "reader"


def test_bearer_token_api_authentication(app_with_tokens: Flask) -> None:
    """Verify that Authorization: Bearer ark_... authenticates API requests."""
    with app_with_tokens.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "reader"))
        token_obj, raw_token = DeviceToken.create_token(
            user_id=user.id, name="Test Token"
        )
        db.session.add(token_obj)
        db.session.commit()
        token_id = token_obj.id

    client = app_with_tokens.test_client()

    # 1. Access without auth -> 401
    res_no_auth = client.get("/api/media")
    assert res_no_auth.status_code == 401

    # 2. Access with Bearer token -> 200
    res_auth = client.get(
        "/api/media",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert res_auth.status_code == 200
    data = res_auth.get_json()
    assert "items" in data

    # 3. Verify last_used_at was updated
    with app_with_tokens.app_context():
        t = db.session.get(DeviceToken, token_id)
        assert t is not None
        assert t.last_used_at is not None


def test_expired_bearer_token_rejected(app_with_tokens: Flask) -> None:
    """Verify expired Bearer tokens return 401."""
    with app_with_tokens.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "reader"))
        token_obj, raw_token = DeviceToken.create_token(
            user_id=user.id, name="Expired Token"
        )
        token_obj.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=1
        )
        db.session.add(token_obj)
        db.session.commit()

    client = app_with_tokens.test_client()
    res = client.get(
        "/api/media",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert res.status_code == 401
    assert "expired" in res.get_json()["error"].lower()


def test_invalid_bearer_token_rejected(app_with_tokens: Flask) -> None:
    """Verify invalid or non-existent Bearer tokens return 401."""
    client = app_with_tokens.test_client()
    res = client.get(
        "/api/media",
        headers={"Authorization": "Bearer ark_invalidnonexistentsecret"},
    )
    assert res.status_code == 401
    assert "invalid" in res.get_json()["error"].lower()


def test_token_crud_api(app_with_tokens: Flask) -> None:
    """Verify token listing, creation, and revocation via REST API."""
    client = app_with_tokens.test_client()
    client.post("/auth/login", data={"username": "reader", "password": "readerpass"})

    # 1. Create a token via POST /api/tokens
    create_res = client.post(
        "/api/tokens",
        json={"name": "Phone App", "scopes": ["read"], "expires_in_days": 60},
    )
    assert create_res.status_code == 201
    created = create_res.get_json()
    assert created["status"] == "success"
    assert created["token"].startswith("ark_")
    token_id = created["token_id"]

    # 2. List tokens via GET /api/tokens
    list_res = client.get("/api/tokens")
    assert list_res.status_code == 200
    tokens = list_res.get_json()["tokens"]
    assert any(t["id"] == token_id for t in tokens)

    # 3. Revoke token via DELETE /api/tokens/<id>
    del_res = client.delete(f"/api/tokens/{token_id}")
    assert del_res.status_code == 200

    # 4. Verify token was deleted and no longer works
    list_res2 = client.get("/api/tokens")
    assert not any(t["id"] == token_id for t in list_res2.get_json()["tokens"])

    client_anon = app_with_tokens.test_client()
    verify_res = client_anon.get(
        "/api/media",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert verify_res.status_code == 401


def test_token_scope_enforcement(app_with_tokens: Flask, tmp_path: Path) -> None:
    """Verify that device tokens strictly enforce declared scopes."""
    with app_with_tokens.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "reader"))
        admin = db.session.scalar(db.select(User).where(User.username == "admin"))
        book = db.session.scalar(db.select(MediaItem).where(MediaItem.title == "Dune"))
        assert user is not None and admin is not None and book is not None

        # Create dummy file so file access doesn't 404 before scope check
        file_path = Path(book.original_file_path)
        file_path.write_bytes(b"PK0304dummyepubdata")

        # 1. Read-only token (only media:read)
        t_readonly, secret_readonly = DeviceToken.create_token(
            user_id=user.id, name="Read Only", scopes=["media:read"]
        )
        # 2. Streamer token (media:read, media:stream)
        t_streamer, secret_streamer = DeviceToken.create_token(
            user_id=user.id,
            name="Streamer",
            scopes=["media:read", "media:stream"],
        )
        # 3. E-reader token (media:read, progress:write)
        t_ereader, secret_ereader = DeviceToken.create_token(
            user_id=user.id,
            name="E-Reader",
            scopes=["media:read", "progress:write"],
        )
        # 4. Admin token for admin user
        t_admin, secret_admin = DeviceToken.create_token(
            user_id=admin.id, name="Admin Tool", scopes=["admin"]
        )
        # 5. Wildcard token
        t_wildcard, secret_wildcard = DeviceToken.create_token(
            user_id=admin.id, name="Full Admin", scopes=["*"]
        )
        db.session.add_all([t_readonly, t_streamer, t_ereader, t_admin, t_wildcard])
        db.session.commit()
        book_id = book.id

    client = app_with_tokens.test_client()

    # Read-only token tests:
    # Allowed: GET /api/media, GET /api/media/<id>, GET /api/media/<id>/progress
    r1 = client.get(
        "/api/media", headers={"Authorization": f"Bearer {secret_readonly}"}
    )
    assert r1.status_code == 200

    r2 = client.get(
        f"/api/media/{book_id}",
        headers={"Authorization": f"Bearer {secret_readonly}"},
    )
    assert r2.status_code == 200

    r3 = client.get(
        f"/api/media/{book_id}/progress",
        headers={"Authorization": f"Bearer {secret_readonly}"},
    )
    assert r3.status_code == 200

    # Denied: GET /api/media/<id>/file (needs media:stream) -> 403
    r4 = client.get(
        f"/api/media/{book_id}/file",
        headers={"Authorization": f"Bearer {secret_readonly}"},
    )
    assert r4.status_code == 403
    assert "media:stream" in r4.get_json()["error"]

    # Denied: POST /api/media/<id>/progress (needs progress:write) -> 403
    r5 = client.post(
        f"/api/media/{book_id}/progress",
        headers={"Authorization": f"Bearer {secret_readonly}"},
        json={"progress": 0.5},
    )
    assert r5.status_code == 403
    assert "progress:write" in r5.get_json()["error"]

    # Streamer token: Can stream file, but cannot write progress
    r6 = client.get(
        f"/api/media/{book_id}/file",
        headers={"Authorization": f"Bearer {secret_streamer}"},
    )
    assert r6.status_code in (200, 206)

    r7 = client.post(
        f"/api/media/{book_id}/progress",
        headers={"Authorization": f"Bearer {secret_streamer}"},
        json={"progress": 0.5},
    )
    assert r7.status_code == 403

    # E-reader token: Can write progress, but cannot stream original binary
    r8 = client.post(
        f"/api/media/{book_id}/progress",
        headers={"Authorization": f"Bearer {secret_ereader}"},
        json={"progress": 0.5},
    )
    assert r8.status_code == 200

    # Admin scope check: Reader with non-admin token cannot access admin routes
    r9 = client.post(
        "/api/backup",
        headers={"Authorization": f"Bearer {secret_readonly}"},
    )
    assert r9.status_code == 403

    # Wildcard token has access to admin
    r10 = client.get(
        "/api/backup",
        headers={"Authorization": f"Bearer {secret_wildcard}"},
    )
    assert r10.status_code == 200
