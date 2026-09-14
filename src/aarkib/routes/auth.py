from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from http import HTTPStatus
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
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select

from aarkib.extensions import db, login_manager, safe_commit
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
    from aarkib.services.security import auth_rate_limiter, get_client_ip

    client_ip = get_client_ip()
    limited, _ = auth_rate_limiter.is_rate_limited(client_ip)
    if limited:
        return None

    auth = req.authorization
    if auth and auth.username:
        user = db.session.scalar(select(User).where(User.username == auth.username))
        allow_remote_pwless = current_app.config.get("ALLOW_PASSWORDLESS_REMOTE", False)
        if user and user.check_password(
            auth.password or "",
            client_ip=client_ip,
            allow_remote_passwordless=allow_remote_pwless,
        ):
            auth_rate_limiter.reset(client_ip)
            return user
        auth_rate_limiter.record_failure(client_ip)
    return None


def require_auth(f: Callable[..., Any]) -> Callable[..., Any]:
    """Requires an authenticated user; redirects to initial setup if 0 users, or login."""

    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            user_count = db.session.scalar(select(func.count(User.id))) or 0
            if user_count == 0:
                return redirect(url_for("auth.setup"))
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


# Backward compatibility alias
optional_or_required_auth = require_auth


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
def login() -> ResponseReturnValue:
    """Handle user login authentication and redirect to target page."""
    if request.method == "GET" and current_user.is_authenticated:
        return redirect(url_for("ui.index"))

    user_count = db.session.scalar(select(func.count(User.id))) or 0
    if user_count == 0:
        return redirect(url_for("auth.setup"))

    if request.method == "POST":
        from aarkib.services.security import (
            auth_rate_limiter,
            get_client_ip,
            is_private_or_local_ip,
        )

        client_ip = get_client_ip()
        limited, retry_after = auth_rate_limiter.is_rate_limited(client_ip)
        if limited:
            flash(
                f"Too many failed login attempts. Please wait {retry_after} seconds before trying again.",
                "error",
            )
            return render_template("login.html"), HTTPStatus.TOO_MANY_REQUESTS

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = db.session.scalar(select(User).where(User.username == username))
        allow_remote_pwless = current_app.config.get("ALLOW_PASSWORDLESS_REMOTE", False)
        if user and user.check_password(
            password,
            client_ip=client_ip,
            allow_remote_passwordless=allow_remote_pwless,
        ):
            auth_rate_limiter.reset(client_ip)
            login_user(user, remember=remember)
            next_page = request.args.get("next")
            redirect_target = (
                next_page if is_safe_url(next_page) else url_for("ui.index")
            )
            flash(f"Welcome back, {user.username}!", "success")
            return redirect(redirect_target)

        auth_rate_limiter.record_failure(client_ip)
        if (
            user
            and not user.has_password
            and not allow_remote_pwless
            and not is_private_or_local_ip(client_ip)
        ):
            flash(
                "Passwordless accounts can only log in from a local network.", "error"
            )
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup() -> ResponseReturnValue:
    """Handle initial administrator account setup when database has 0 users."""
    user_count = db.session.scalar(select(func.count(User.id))) or 0
    if user_count > 0:
        if current_user.is_authenticated:
            return redirect(url_for("ui.index"))
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not password:
            flash(
                "Username and password are required for administrator setup.", "error"
            )
        elif len(password) < 4:
            flash("Password must be at least 4 characters long.", "error")
        elif password != confirm_password:
            flash("Passwords do not match.", "error")
        else:
            user = User(username=username, is_admin=True)
            user.set_password(password)
            db.session.add(user)
            safe_commit()
            login_user(user)
            flash("Admin account created! Welcome to Aarkib.", "success")
            return redirect(url_for("ui.index"))

    return render_template("setup.html")


@auth_bp.route("/logout")
@login_required
def logout() -> ResponseReturnValue:
    """Log out the current authenticated user and return to bookshelf."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("ui.index"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile() -> ResponseReturnValue:
    """View and update current user credentials and reading statistics."""
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_new_password = request.form.get("confirm_new_password", "")

        if current_user.has_password and not current_user.check_password(
            current_password
        ):
            flash("Current password is incorrect.", "error")
        elif current_user.is_admin and not new_password:
            flash("Administrators cannot remove their password.", "error")
        elif new_password and len(new_password) < 4:
            flash("New password must be at least 4 characters.", "error")
        elif new_password != confirm_new_password:
            flash("New passwords do not match.", "error")
        else:
            if new_password:
                current_user.set_password(new_password)
                flash("Password updated successfully!", "success")
            else:
                current_user.set_password(None)
                flash("Password removed. Account is now passwordless.", "info")
            safe_commit()
            return redirect(url_for("auth.profile"))

    # Compute reading stats
    completed_count = (
        db.session.scalar(
            select(func.count(UserProgress.id)).where(
                UserProgress.user_id == current_user.id,
                UserProgress.is_completed.is_(True),
            )
        )
        or 0
    )

    reading_count = (
        db.session.scalar(
            select(func.count(UserProgress.id)).where(
                UserProgress.user_id == current_user.id,
                UserProgress.is_completed.is_(False),
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
def manage_users() -> ResponseReturnValue:
    """Manage existing user accounts or create new reader/admin users."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))

        if not username:
            flash("Username is required.", "error")
        elif is_admin and not password:
            flash("A password is required for administrator accounts.", "error")
        elif password and len(password) < 4:
            flash("Password must be at least 4 characters long.", "error")
        elif db.session.scalar(select(User).where(User.username == username)):
            flash(f"User '{username}' already exists.", "error")
        else:
            new_user = User(username=username, is_admin=is_admin)
            if password:
                new_user.set_password(password)
            else:
                new_user.set_password(None)
            db.session.add(new_user)
            safe_commit()
            flash(f"User '{username}' created successfully.", "success")
            referrer = request.referrer or ""
            if "/settings" in referrer:
                return redirect(url_for("ui.settings", category="users"))
            return redirect(url_for("auth.manage_users"))

    users_list = db.session.scalars(select(User).order_by(User.id.asc())).all()
    return render_template(
        "settings/users.html",
        users=users_list,
        active_category="users",
        category_title="User Management",
        is_admin=True,
    )


@auth_bp.route("/users/<int:user_id>/toggle-admin", methods=["POST"])
@admin_required
def toggle_admin(user_id: int) -> ResponseReturnValue:
    """Toggle administrator privileges for a specific user account."""
    target_user = db.session.get(User, user_id)
    referrer = request.referrer or ""
    dest = (
        url_for("ui.settings", category="users")
        if "/settings" in referrer
        else url_for("auth.manage_users")
    )
    if not target_user:
        flash("User not found.", "error")
        return redirect(dest)

    if target_user.id == current_user.id:
        flash("You cannot change your own administrator status.", "error")
        return redirect(dest)

    if not target_user.is_admin and not target_user.has_password:
        flash(
            f"Cannot promote '{target_user.username}' to administrator without a password. Please set a password first.",
            "error",
        )
        return redirect(dest)

    target_user.is_admin = not target_user.is_admin
    safe_commit()
    flash(
        f"Updated admin status for '{target_user.username}' to {target_user.is_admin}.",
        "success",
    )
    return redirect(dest)


@auth_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id: int) -> ResponseReturnValue:
    """Update, set, or remove the password for a specific user account."""
    target_user = db.session.get(User, user_id)
    referrer = request.referrer or ""
    dest = (
        url_for("ui.settings", category="users")
        if "/settings" in referrer
        else url_for("auth.manage_users")
    )
    if not target_user:
        flash("User not found.", "error")
        return redirect(dest)

    new_password = request.form.get("new_password", "")
    if not new_password:
        if target_user.is_admin:
            flash("Administrators cannot have a blank password.", "error")
            return redirect(dest)
        target_user.set_password(None)
        safe_commit()
        flash(
            f"Removed password for '{target_user.username}'. User is now passwordless.",
            "success",
        )
    elif len(new_password) < 4:
        flash("Password must be at least 4 characters long.", "error")
    else:
        target_user.set_password(new_password)
        safe_commit()
        flash(f"Reset password for '{target_user.username}'.", "success")

    return redirect(dest)


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int) -> ResponseReturnValue:
    """Delete a user account and purge all associated reading progress and bookmarks."""
    target_user = db.session.get(User, user_id)
    referrer = request.referrer or ""
    dest = (
        url_for("ui.settings", category="users")
        if "/settings" in referrer
        else url_for("auth.manage_users")
    )
    if not target_user:
        flash("User not found.", "error")
        return redirect(dest)

    if target_user.id == current_user.id:
        flash("You cannot delete your own account while logged in.", "error")
        return redirect(dest)

    username = target_user.username
    db.session.delete(target_user)
    safe_commit()
    flash(f"User '{username}' and their reading progress deleted.", "success")
    return redirect(dest)
