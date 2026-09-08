from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select

from aarkib.extensions import db, login_manager
from aarkib.models import Bookmark, User, UserProgress

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


@login_manager.request_loader
def load_user_from_request(req: Any) -> User | None:
    """Authenticates API and OPDS requests using HTTP Basic Auth."""
    auth = req.authorization
    if auth and auth.username and auth.password:
        user = db.session.scalar(select(User).where(User.username == auth.username))
        if user and user.check_password(auth.password):
            return user
    return None


def optional_or_required_auth(f: Callable[..., Any]) -> Callable[..., Any]:
    """Requires login if AUTH_REQUIRED is enabled in config; otherwise allows guests."""

    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if (
            current_app.config.get("AUTH_REQUIRED", False)
            and not current_user.is_authenticated
        ):
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Requires an authenticated user with administrator privileges."""

    @wraps(f)
    @login_required
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_admin:
            flash("Administrator privileges required to access this page.", "error")
            return redirect(url_for("ui.index"))
        return f(*args, **kwargs)

    return decorated_function


def is_safe_url(target: str | None) -> bool:
    """Verifies that a redirect target URL is safe and relative to this application."""
    if not target:
        return False
    # Disallow backslashes to prevent protocol-relative URL bypasses like /\example.com
    if "\\" in target:
        return False
    ref_url = urlsplit(request.host_url)
    test_url = urlsplit(target)
    return (
        test_url.scheme in ("", "http", "https")
        and (test_url.netloc == "" or test_url.netloc == ref_url.netloc)
        and not target.startswith("//")
        and target.startswith("/")
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("ui.index"))

    user_count = db.session.scalar(select(func.count(User.id))) or 0
    if user_count == 0:
        return redirect(url_for("auth.register"))

    allow_registration = current_app.config.get("ALLOW_REGISTRATION", True)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = db.session.scalar(select(User).where(User.username == username))
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get("next")
            redirect_target = (
                next_page if is_safe_url(next_page) else url_for("ui.index")
            )
            flash(f"Welcome back, {user.username}!", "success")
            return redirect(redirect_target)
        flash("Invalid username or password.", "error")

    return render_template("login.html", allow_registration=allow_registration)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    user_count = db.session.scalar(select(func.count(User.id))) or 0
    is_first_user = user_count == 0
    allow_registration = current_app.config.get("ALLOW_REGISTRATION", True)

    if not is_first_user and not allow_registration:
        flash(
            "Public registration is disabled. Please contact your administrator.",
            "error",
        )
        return redirect(url_for("auth.login"))

    if current_user.is_authenticated:
        return redirect(url_for("ui.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
        elif len(password) < 4:
            flash("Password must be at least 4 characters long.", "error")
        elif password != confirm_password:
            flash("Passwords do not match.", "error")
        elif db.session.scalar(select(User).where(User.username == username)):
            flash("Username is already taken.", "error")
        else:
            user = User(username=username, is_admin=is_first_user)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(
                "Admin account created! Welcome to Aarkib."
                if is_first_user
                else "Account registered successfully!",
                "success",
            )
            return redirect(url_for("ui.index"))

    return render_template("register.html", is_first_user=is_first_user)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("ui.index"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_new_password = request.form.get("confirm_new_password", "")

        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 4:
            flash("New password must be at least 4 characters.", "error")
        elif new_password != confirm_new_password:
            flash("New passwords do not match.", "error")
        else:
            current_user.set_password(new_password)
            db.session.commit()
            flash("Password updated successfully!", "success")
            return redirect(url_for("auth.profile"))

    # Compute reading stats
    completed_count = (
        db.session.scalar(
            select(func.count(UserProgress.id)).where(
                UserProgress.user_id == current_user.id,
                UserProgress.is_completed == True,  # noqa: E712
            )
        )
        or 0
    )

    reading_count = (
        db.session.scalar(
            select(func.count(UserProgress.id)).where(
                UserProgress.user_id == current_user.id,
                UserProgress.is_completed == False,  # noqa: E712
                UserProgress.percentage > 0,
            )
        )
        or 0
    )

    bookmarks_count = (
        db.session.scalar(
            select(func.count(Bookmark.id)).where(Bookmark.user_id == current_user.id)
        )
        or 0
    )

    return render_template(
        "profile.html",
        user=current_user,
        completed_count=completed_count,
        reading_count=reading_count,
        bookmarks_count=bookmarks_count,
    )


@auth_bp.route("/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            flash("Username and password are required.", "error")
        elif db.session.scalar(select(User).where(User.username == username)):
            flash(f"User '{username}' already exists.", "error")
        else:
            new_user = User(username=username, is_admin=is_admin)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            flash(f"User '{username}' created successfully.", "success")
            return redirect(url_for("auth.manage_users"))

    users_list = db.session.scalars(select(User).order_by(User.id.asc())).all()
    return render_template("users.html", users=users_list)


@auth_bp.route("/users/<int:user_id>/toggle-admin", methods=["POST"])
@admin_required
def toggle_admin(user_id: int):
    target_user = db.session.get(User, user_id)
    if not target_user:
        flash("User not found.", "error")
        return redirect(url_for("auth.manage_users"))

    if target_user.id == current_user.id:
        flash("You cannot change your own administrator status.", "error")
        return redirect(url_for("auth.manage_users"))

    target_user.is_admin = not target_user.is_admin
    db.session.commit()
    flash(
        f"Updated admin status for '{target_user.username}' to {target_user.is_admin}.",
        "success",
    )
    return redirect(url_for("auth.manage_users"))


@auth_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id: int):
    target_user = db.session.get(User, user_id)
    if not target_user:
        flash("User not found.", "error")
        return redirect(url_for("auth.manage_users"))

    new_password = request.form.get("new_password", "")
    if not new_password or len(new_password) < 4:
        flash("Password must be at least 4 characters long.", "error")
    else:
        target_user.set_password(new_password)
        db.session.commit()
        flash(f"Reset password for '{target_user.username}'.", "success")

    return redirect(url_for("auth.manage_users"))


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int):
    target_user = db.session.get(User, user_id)
    if not target_user:
        flash("User not found.", "error")
        return redirect(url_for("auth.manage_users"))

    if target_user.id == current_user.id:
        flash("You cannot delete your own account while logged in.", "error")
        return redirect(url_for("auth.manage_users"))

    username = target_user.username
    db.session.delete(target_user)
    db.session.commit()
    flash(f"User '{username}' and their reading progress deleted.", "success")
    return redirect(url_for("auth.manage_users"))
