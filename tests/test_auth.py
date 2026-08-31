from sqlalchemy import select

from buukuu.extensions import db
from buukuu.models import User


def test_user_registration_and_login(client):
    # 1. Register first user (should become admin)
    res = client.post(
        "/auth/register",
        data={
            "username": "adminuser",
            "password": "secretpassword",
            "confirm_password": "secretpassword",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert b"adminuser" in res.data

    user = db.session.scalar(select(User).where(User.username == "adminuser"))
    assert user is not None
    assert user.is_admin is True

    # Logout
    res = client.get("/auth/logout", follow_redirects=True)
    assert res.status_code == 200
    assert b"logged out" in res.data

    # 2. Register second user (standard reader)
    res = client.post(
        "/auth/register",
        data={
            "username": "reader1",
            "password": "readerpassword",
            "confirm_password": "readerpassword",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    reader = db.session.scalar(select(User).where(User.username == "reader1"))
    assert reader is not None
    assert reader.is_admin is False

    # Logout and login back as reader1
    client.get("/auth/logout", follow_redirects=True)

    res = client.post(
        "/auth/login",
        data={"username": "reader1", "password": "wrongpassword"},
        follow_redirects=True,
    )
    assert b"Invalid username or password" in res.data

    res = client.post(
        "/auth/login",
        data={"username": "reader1", "password": "readerpassword"},
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert b"reader1" in res.data


def test_user_profile_and_change_password(client):
    # Create user
    user = User(username="profileuser", is_admin=False)
    user.set_password("oldpass123")
    db.session.add(user)
    db.session.commit()

    # Login
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

    # Toggle admin role
    res = client.post(f"/auth/users/{regular.id}/toggle-admin", follow_redirects=True)
    assert res.status_code == 200
    assert b"Updated admin status" in res.data
    db.session.refresh(regular)
    assert regular.is_admin is True

    # Reset password
    res = client.post(
        f"/auth/users/{regular.id}/reset-password",
        data={"new_password": "resetpassword123"},
        follow_redirects=True,
    )
    assert b"Reset password" in res.data
    db.session.refresh(regular)
    assert regular.check_password("resetpassword123") is True

    # Delete user
    res = client.post(f"/auth/users/{regular.id}/delete", follow_redirects=True)
    assert b"deleted" in res.data
    assert db.session.get(User, regular.id) is None


def test_per_user_progress_isolation(client, app, sample_epub):
    from pathlib import Path

    from buukuu.services.scanner import index_single_book

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
        f"/api/books/{book_id}/progress",
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
        f"/api/books/{book_id}/progress",
        json={"location": "cfi_bob", "percentage": 80.0},
    )
    assert res.status_code == 200
    assert res.get_json()["percentage"] == 80.0

    # Verify Bob's progress is 80%
    res = client.get(f"/api/books/{book_id}/progress")
    assert res.get_json()["percentage"] == 80.0

    client.get("/auth/logout", follow_redirects=True)

    # Login Alice again and verify her progress is still 35%
    client.post(
        "/auth/login",
        data={"username": "alice", "password": "passalice"},
        follow_redirects=True,
    )
    res = client.get(f"/api/books/{book_id}/progress")
    assert res.get_json()["percentage"] == 35.0


def test_auth_required_enforcement(client, app, sample_epub):
    from pathlib import Path

    from buukuu.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

        # Create a user so registration is not forced
        user = User(username="auth_tester", is_admin=False)
        user.set_password("pass123")
        db.session.add(user)
        db.session.commit()

    app.config["AUTH_REQUIRED"] = True

    # 1. Unauthenticated requests to UI should redirect to /auth/login
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/login" in res.headers["Location"]

    res = client.get(f"/book/{book_id}", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/login" in res.headers["Location"]

    res = client.get(f"/reader/epub/{book_id}", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/login" in res.headers["Location"]

    # 2. Unauthenticated request to API should return 401
    res = client.get("/api/books")
    assert res.status_code == 401

    # 3. Log in as user
    client.post(
        "/auth/login",
        data={"username": "auth_tester", "password": "pass123"},
        follow_redirects=True,
    )

    # 4. Authenticated requests succeed
    res = client.get("/")
    assert res.status_code == 200
    assert b"Library" in res.data

    res = client.get(f"/book/{book_id}")
    assert res.status_code == 200
    assert b"Sample Test Book" in res.data

    res = client.get("/api/books")
    assert res.status_code == 200
    assert len(res.get_json()["books"]) >= 1
