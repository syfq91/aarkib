from __future__ import annotations

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "info"


def safe_commit() -> None:
    """Commit the active session transaction with automatic rollback on error."""
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
