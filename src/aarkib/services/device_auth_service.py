"""Service for TV device code pairing and mobile API authentication."""

from __future__ import annotations

import datetime
import secrets
from typing import Any

from sqlalchemy import select

from aarkib.extensions import db, safe_commit
from aarkib.models.token import DevicePairingCode, DeviceToken
from aarkib.models.user import User

# Ambiguous characters omitted: 0, O, 1, I, L
USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_user_code() -> str:
    """Generate a clean, unambiguous 6-character user pairing code (e.g. ABC-123)."""
    part1 = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(3))
    part2 = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(3))
    return f"{part1}-{part2}"


def create_device_pairing_code(
    device_name: str | None = None,
    expires_in_seconds: int = 300,
) -> tuple[DevicePairingCode, str, str]:
    """Create a new TV device pairing session."""
    device_code = secrets.token_hex(32)
    user_code = generate_user_code()

    # Ensure user_code uniqueness among active codes
    now_utc = datetime.datetime.now(datetime.UTC)
    expires_at = now_utc + datetime.timedelta(seconds=expires_in_seconds)

    pairing_obj = DevicePairingCode(
        device_code=device_code,
        user_code=user_code,
        device_name=(device_name or "TV Device").strip()[:128],
        expires_at=expires_at,
        is_authorized=False,
    )
    db.session.add(pairing_obj)
    safe_commit()

    return pairing_obj, device_code, user_code


def authorize_device_pairing_code(user_code: str, user_id: int) -> tuple[bool, str]:
    """Authorize a TV pairing code on behalf of an authenticated user."""
    cleaned_code = user_code.strip().upper().replace(" ", "")
    # Allow input with or without hyphen
    if len(cleaned_code) == 6 and "-" not in cleaned_code:
        cleaned_code = f"{cleaned_code[:3]}-{cleaned_code[3:]}"

    code_obj = db.session.scalar(
        select(DevicePairingCode).where(DevicePairingCode.user_code == cleaned_code)
    )
    if not code_obj:
        return False, "Invalid pairing code"

    if code_obj.is_expired():
        return False, "Pairing code has expired. Please request a new code on your TV."

    if code_obj.is_authorized:
        return False, "Pairing code has already been authorized"

    user = db.session.get(User, user_id)
    if not user:
        return False, "User not found"

    # Create persistent device token for this device
    token_obj, raw_token = DeviceToken.create_token(
        user_id=user.id,
        name=code_obj.device_name or "TV Client",
        scopes=["*"],
    )
    db.session.add(token_obj)

    code_obj.user_id = user.id
    code_obj.is_authorized = True
    code_obj.raw_token = raw_token
    safe_commit()

    return True, "Device authorized successfully"


def poll_device_pairing_code(device_code: str) -> dict[str, Any]:
    """Check status of TV pairing session."""
    code_obj = db.session.scalar(
        select(DevicePairingCode).where(DevicePairingCode.device_code == device_code)
    )
    if not code_obj:
        return {
            "status": "error",
            "error": "invalid_code",
            "message": "Pairing session not found",
        }

    if code_obj.is_expired():
        return {
            "status": "error",
            "error": "expired_code",
            "message": "Pairing session expired",
        }

    if not code_obj.is_authorized or not code_obj.raw_token:
        return {
            "status": "pending",
            "error": "authorization_pending",
            "message": "Authorization pending",
            "interval": 5,
        }

    # Authorized! Retrieve token and clear single-use raw token
    user = db.session.get(User, code_obj.user_id) if code_obj.user_id else None
    token = code_obj.raw_token

    # Consume single-use token from pairing record
    code_obj.raw_token = None
    safe_commit()

    return {
        "status": "success",
        "token": token,
        "access_token": token,
        "token_type": "Bearer",
        "user": {
            "id": user.id if user else None,
            "username": user.username if user else None,
            "is_admin": user.is_admin if user else False,
        },
    }
