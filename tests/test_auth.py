import base64
from pathlib import Path

from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import User
from aarkib.routes.auth import is_safe_url
from aarkib.services.scanner import index_single_book


def test_first_time_setup_workflow(unauth_client):
    # 1. On empty database (0 users), accessing / redirects to /auth/setup
    res = unauth_client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/setup" in res.headers["Location"]

    # 2. Access /auth/setup
    res = unauth_client.get("/auth/setup")
    assert res.status_code == 200
    assert b"Welcome to Aarkib" in res.data
    assert b"Administrator Username" in res.data

    # 3. Attempt to create admin with blank password -> fails (compulsory for admin)
    res_blank = unauth_client.post(
        "/auth/setup",
        data={
            "username": "adminuser",
            "password": "",
            "confirm_password": "",
        },
        follow_redirects=True,
    )
    assert b"required" in res_blank.data

    # 4. Create primary administrator with valid password
    res = unauth_client.post(
        "/auth/setup",
        data={
            "username": "adminuser",
            "password": "secretpassword",
            "confirm_password": "secretpassword",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert b"Admin account created" in res.data

    user = db.session.scalar(select(User).where(User.username == "adminuser"))
    assert user is not None
    assert user.is_admin is True
    assert user.has_password is True
    assert user.check_password("secretpassword") is True

    # 5. Once users exist, /auth/setup is locked out
    res_setup_again = unauth_client.get("/auth/setup", follow_redirects=False)
    assert res_setup_again.status_code == 302

    # 6. /auth/register is permanently removed (404)
    res_reg = unauth_client.get("/auth/register")
    assert res_reg.status_code == 404


def test_passwordless_normal_user_login(client):
    # 1. Admin creates a passwordless reader user
    res = client.post(
        "/auth/users",
        data={"username": "nopassreader", "password": "", "is_admin": ""},
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert b"nopassreader" in res.data

    reader = db.session.scalar(select(User).where(User.username == "nopassreader"))
    assert reader is not None
    assert reader.is_admin is False
    assert reader.has_password is False
    assert reader.password_hash is None
    assert reader.check_password("") is True
    assert reader.check_password("any_random_pass") is False

    # 2. Log out
    client.get("/auth/logout", follow_redirects=True)

    # 3. Attempt login with an arbitrary non-blank password -> fails
    res_fail = client.post(
        "/auth/login",
        data={"username": "nopassreader", "password": "wrongpassword"},
        follow_redirects=True,
    )
    assert b"Invalid username or password" in res_fail.data

    # 4. Sign in with blank password -> succeeds
    res_login = client.post(
        "/auth/login",
        data={"username": "nopassreader", "password": ""},
        follow_redirects=True,
    )
    assert res_login.status_code == 200
    assert b"Welcome back, nopassreader" in res_login.data


def test_user_profile_and_change_password(client):
    # 1. Create a regular user
    user = User(username="profileuser", is_admin=False)
    user.set_password("oldpass123")
    db.session.add(user)
    db.session.commit()

    # Login as regular user
    client.post(
        "/auth/login",
        data={"username": "profileuser", "password": "oldpass123"},
        follow_redirects=True,
    )

    # View Profile
    res = client.get("/auth/profile")
    assert res.status_code == 200
    assert b"profileuser" in res.data

    # Change password with wrong current password
    res = client.post(
        "/auth/profile",
        data={
            "current_password": "wrong",
            "new_password": "newpass123",
            "confirm_new_password": "newpass123",
        },
        follow_redirects=True,
    )
    assert b"Current password is incorrect" in res.data

    # Change password successfully
    res = client.post(
        "/auth/profile",
        data={
            "current_password": "oldpass123",
            "new_password": "newpass123",
            "confirm_new_password": "newpass123",
        },
        follow_redirects=True,
    )
    assert b"Password updated successfully" in res.data

    # Verify new password
    updated_user = db.session.scalar(select(User).where(User.username == "profileuser"))
    assert updated_user.check_password("newpass123") is True

    # Regular user can remove password (make account passwordless)
    res_remove = client.post(
        "/auth/profile",
        data={
            "current_password": "newpass123",
            "new_password": "",
            "confirm_new_password": "",
        },
        follow_redirects=True,
    )
    assert res_remove.status_code == 200
    assert b"Password removed" in res_remove.data
    db.session.refresh(updated_user)
    assert updated_user.has_password is False
    assert updated_user.check_password("") is True

    # 2. Administrator cannot remove password in profile
    client.get("/auth/logout", follow_redirects=True)
    admin = User(username="admin_prof", is_admin=True)
    admin.set_password("adminpass")
    db.session.add(admin)
    db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "admin_prof", "password": "adminpass"},
        follow_redirects=True,
    )
    res_admin_remove = client.post(
        "/auth/profile",
        data={
            "current_password": "adminpass",
            "new_password": "",
            "confirm_new_password": "",
        },
        follow_redirects=True,
    )
    assert b"Administrators cannot remove their password" in res_admin_remove.data


def test_admin_user_management(client):
    # Create admin
    admin = User(username="superadmin", is_admin=True)
    admin.set_password("adminpass")
    # Create regular user
    regular = User(username="user_to_manage", is_admin=False)
    regular.set_password("userpass")
    db.session.add_all([admin, regular])
    db.session.commit()

    # Try accessing /auth/users as unauthenticated user -> redirect
    client.get("/auth/logout", follow_redirects=True)
    res = client.get("/auth/users")
    assert res.status_code == 302

    # Login as regular user -> denied
    client.post(
        "/auth/login",
        data={"username": "user_to_manage", "password": "userpass"},
        follow_redirects=True,
    )
    res = client.get("/auth/users", follow_redirects=True)
    assert b"Administrator privileges required" in res.data

    client.get("/auth/logout", follow_redirects=True)

    # Login as admin -> allowed
    client.post(
        "/auth/login",
        data={"username": "superadmin", "password": "adminpass"},
        follow_redirects=True,
    )
    res = client.get("/auth/users")
    assert res.status_code == 200
    assert b"user_to_manage" in res.data

    # Admin cannot create another admin without a password
    res_create_admin_blank = client.post(
        "/auth/users",
        data={"username": "blank_admin", "password": "", "is_admin": "1"},
        follow_redirects=True,
    )
    assert (
        b"password is required for administrator" in res_create_admin_blank.data.lower()
    )

    # Admin creates a passwordless regular user
    res_create_pwless = client.post(
        "/auth/users",
        data={"username": "pwless_reader", "password": "", "is_admin": ""},
        follow_redirects=True,
    )
    assert res_create_pwless.status_code == 200
    assert b"pwless_reader" in res_create_pwless.data
    pwless_u = db.session.scalar(select(User).where(User.username == "pwless_reader"))
    assert pwless_u is not None
    assert pwless_u.has_password is False

    # Cannot promote passwordless user to admin without a password
    res_promo_fail = client.post(
        f"/auth/users/{pwless_u.id}/toggle-admin", follow_redirects=True
    )
    assert b"without a password" in res_promo_fail.data
    db.session.refresh(pwless_u)
    assert pwless_u.is_admin is False

    # Admin cannot reset an admin's password to blank
    regular.is_admin = True
    db.session.commit()
    res_reset_target_admin = client.post(
        f"/auth/users/{regular.id}/reset-password",
        data={"new_password": ""},
        follow_redirects=True,
    )
    assert b"Administrators cannot have a blank password" in res_reset_target_admin.data
    regular.is_admin = False
    db.session.commit()

    # Reset regular user's password with blank to remove password (allowed for regular user)
    res = client.post(
        f"/auth/users/{regular.id}/reset-password",
        data={"new_password": ""},
        follow_redirects=True,
    )
    assert b"now passwordless" in res.data
    db.session.refresh(regular)
    assert regular.has_password is False
    assert regular.check_password("") is True

    # Delete user
    res = client.post(f"/auth/users/{regular.id}/delete", follow_redirects=True)
    assert b"deleted" in res.data
    assert db.session.get(User, regular.id) is None


def test_per_user_progress_isolation(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

        user_alice = User(username="alice", is_admin=False)
        user_alice.set_password("passalice")
        user_bob = User(username="bob", is_admin=False)
        user_bob.set_password("passbob")
        db.session.add_all([user_alice, user_bob])
        db.session.commit()

    # Login Alice and save progress at 35%
    client.post(
        "/auth/login",
        data={"username": "alice", "password": "passalice"},
        follow_redirects=True,
    )
    res = client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "cfi_alice", "percentage": 35.0},
    )
    assert res.status_code == 200
    assert res.get_json()["percentage"] == 35.0

    client.get("/auth/logout", follow_redirects=True)

    # Login Bob and save progress at 80%
    client.post(
        "/auth/login",
        data={"username": "bob", "password": "passbob"},
        follow_redirects=True,
    )
    res = client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "cfi_bob", "percentage": 80.0},
    )
    assert res.status_code == 200
    assert res.get_json()["percentage"] == 80.0

    # Verify Bob's progress is 80%
    res = client.get(f"/api/media/{book_id}/progress")
    assert res.get_json()["percentage"] == 80.0

    client.get("/auth/logout", follow_redirects=True)

    # Login Alice again and verify her progress is still 35%
    client.post(
        "/auth/login",
        data={"username": "alice", "password": "passalice"},
        follow_redirects=True,
    )
    res = client.get(f"/api/media/{book_id}/progress")
    assert res.get_json()["percentage"] == 35.0


def test_mandatory_auth_enforcement(unauth_client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

        # Create user with password and passwordless user
        user = User(username="auth_tester", is_admin=False)
        user.set_password("pass123")
        pwless_user = User(username="auth_pwless", is_admin=False)
        pwless_user.set_password(None)
        db.session.add_all([user, pwless_user])
        db.session.commit()

    # 1. Unauthenticated requests to UI should redirect to /auth/login
    res = unauth_client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/login" in res.headers["Location"]

    res = unauth_client.get(f"/media/{book_id}", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/login" in res.headers["Location"]

    res = unauth_client.get(f"/reader/epub/{book_id}", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/login" in res.headers["Location"]

    # 2. Unauthenticated request to API should return 401
    res = unauth_client.get("/api/media")
    assert res.status_code == 401

    # 3. Log in as user
    unauth_client.post(
        "/auth/login",
        data={"username": "auth_tester", "password": "pass123"},
        follow_redirects=True,
    )

    # 4. Authenticated requests succeed
    res = unauth_client.get("/")
    assert res.status_code == 200
    assert b"Library" in res.data

    res = unauth_client.get(f"/media/{book_id}")
    assert res.status_code == 200
    assert b"Sample Test Book" in res.data

    res = unauth_client.get("/api/media")
    assert res.status_code == 200
    assert len(res.get_json()["items"]) >= 1

    # 5. Public healthcheck endpoint succeeds without auth
    unauth_client.get("/auth/logout", follow_redirects=True)
    res_health = unauth_client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.get_json()["status"] == "healthy"

    # 6. HTTP Basic Auth with password
    b64_creds = base64.b64encode(b"auth_tester:pass123").decode("ascii")
    headers = {"Authorization": f"Basic {b64_creds}"}

    res_api = unauth_client.get("/api/media", headers=headers)
    assert res_api.status_code == 200

    # Post progress via Basic Auth
    res_prog = unauth_client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "cfi_basic_auth", "percentage": 77.0},
        headers=headers,
    )
    assert res_prog.status_code == 200
    assert res_prog.get_json()["percentage"] == 77.0

    # 7. HTTP Basic Auth with passwordless user (blank password)
    b64_pwless = base64.b64encode(b"auth_pwless:").decode("ascii")
    headers_pwless = {"Authorization": f"Basic {b64_pwless}"}
    res_pwless = unauth_client.get("/api/media", headers=headers_pwless)
    assert res_pwless.status_code == 200


def test_registration_route_is_removed(client):
    assert client.get("/auth/register").status_code == 404
    assert client.post("/auth/register", data={"username": "foo"}).status_code == 404


def test_is_safe_url_backslash_rejection(app):
    with app.test_request_context("/"):
        assert is_safe_url("/books") is True
        assert is_safe_url("/settings") is True
        assert is_safe_url("//attacker.com") is False
        assert is_safe_url("/\\attacker.com") is False
        assert is_safe_url("\\attacker.com") is False
        assert is_safe_url("https://attacker.com") is False
        assert is_safe_url("") is False
        assert is_safe_url(None) is False
