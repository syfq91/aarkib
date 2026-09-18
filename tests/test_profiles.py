"""Comprehensive tests for User Profiles, Library ACLs, and Centralized Authorization."""

from __future__ import annotations

from pathlib import Path

from aarkib import create_app, migrate_database
from aarkib.config import TestConfig
from aarkib.extensions import db
from aarkib.models import (
    Bookmark,
    Library,
    MediaItem,
    Profile,
    ProfileLibraryAccess,
    Tag,
    User,
    UserProgress,
)
from aarkib.services.authorization import authorization
from aarkib.services.progress_service import (
    add_bookmark,
    get_progress,
    list_bookmarks,
    update_progress,
)


def test_profile_model_creation_and_attributes(app):
    """Verify Profile and ProfileLibraryAccess instantiation, relationships, and serialization."""
    with app.app_context():
        user = User(username="family_head")
        user.set_password("secret123")
        db.session.add(user)
        db.session.commit()

        prof = Profile(
            user_id=user.id, name="Kiddo", is_child=True, avatar_url="/img/kid.png"
        )
        db.session.add(prof)
        db.session.commit()

        assert prof.id is not None
        assert prof.user_id == user.id
        assert prof.is_child is True
        assert prof.name == "Kiddo"

        # Check serialization
        p_dict = prof.to_dict()
        assert p_dict["name"] == "Kiddo"
        assert p_dict["is_child"] is True
        assert p_dict["avatar_url"] == "/img/kid.png"
        assert p_dict["user_id"] == user.id

        # ProfileLibraryAccess
        lib = Library(
            slug="cartoons", name="Cartoons", path="/tmp/cartoons", media_type="video"
        )
        db.session.add(lib)
        db.session.commit()

        acl = ProfileLibraryAccess(
            profile_id=prof.id, library_id=lib.id, can_read=True, can_download=False
        )
        db.session.add(acl)
        db.session.commit()

        assert acl.can_read is True
        assert acl.can_download is False
        assert len(prof.library_access) == 1
        assert prof.to_dict()["library_access"][0]["can_download"] is False


def test_default_profile_auto_migration(tmp_path: Path):
    """Verify non-destructive migration automatically creates default profile and links progress."""

    class CustomConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/migration_test.db"

    test_app = create_app(CustomConfig)
    with test_app.app_context():
        # Create user directly
        user = User(username="legacy_user")
        db.session.add(user)
        db.session.commit()

        item = MediaItem(
            original_file_path="/tmp/item.epub",
            title="Legacy Book",
            file_hash="hash_legacy",
            file_format="epub",
        )
        db.session.add(item)
        db.session.commit()

        # Add progress and bookmark without profile_id (simulating legacy data)
        prog = UserProgress(user_id=user.id, media_item_id=item.id, percentage=50.0)
        bm = Bookmark(user_id=user.id, media_item_id=item.id, location="loc_1")
        db.session.add_all([prog, bm])
        db.session.commit()

        # Trigger auto-migration
        migrate_database()

        # Re-fetch user and verify Default profile was created
        db.session.expire_all()
        refreshed_user = db.session.get(User, user.id)
        assert len(refreshed_user.profiles) == 1
        default_prof = refreshed_user.default_profile
        assert default_prof is not None
        assert default_prof.name == "Default"

        # Verify progress and bookmark records were backfilled with profile_id
        refreshed_prog = db.session.get(UserProgress, prog.id)
        refreshed_bm = db.session.get(Bookmark, bm.id)
        assert refreshed_prog.profile_id == default_prof.id
        assert refreshed_bm.profile_id == default_prof.id


def test_profile_progress_and_bookmark_isolation(app):
    """Verify independent progress tracking and bookmarks per profile under the same user."""
    with app.app_context():
        user = User(username="shared_account")
        db.session.add(user)
        db.session.commit()

        prof_parent = Profile(user_id=user.id, name="Parent", is_child=False)
        prof_child = Profile(user_id=user.id, name="Child", is_child=True)
        db.session.add_all([prof_parent, prof_child])
        db.session.commit()

        item = MediaItem(
            original_file_path="/tmp/book.epub",
            title="Harry Potter",
            file_hash="hash_hp",
            file_format="epub",
        )
        db.session.add(item)
        db.session.commit()

        # Parent reads to 80%
        update_progress(
            user.id,
            item,
            {"location": "chapter_8", "percentage": 80.0},
            profile_id=prof_parent.id,
        )
        # Child reads to 15%
        update_progress(
            user.id,
            item,
            {"location": "chapter_2", "percentage": 15.0},
            profile_id=prof_child.id,
        )

        parent_progress = get_progress(user.id, item, profile_id=prof_parent.id)
        child_progress = get_progress(user.id, item, profile_id=prof_child.id)

        assert parent_progress["percentage"] == 80.0
        assert parent_progress["location"] == "chapter_8"

        assert child_progress["percentage"] == 15.0
        assert child_progress["location"] == "chapter_2"

        # Bookmarks isolation
        add_bookmark(
            item.id, user.id, "quote_1", title="Parent Note", profile_id=prof_parent.id
        )
        add_bookmark(
            item.id, user.id, "pic_1", title="Child Drawing", profile_id=prof_child.id
        )

        parent_bms = list_bookmarks(item.id, profile_id=prof_parent.id)
        child_bms = list_bookmarks(item.id, profile_id=prof_child.id)

        assert len(parent_bms) == 1
        assert parent_bms[0].title == "Parent Note"

        assert len(child_bms) == 1
        assert child_bms[0].title == "Child Drawing"


def test_authorization_service_roles_and_actions(app):
    """Verify role and action checks in Centralized AuthorizationService."""
    with app.app_context():
        admin = User(username="admin_boss", is_admin=True)
        regular = User(username="regular_joe", is_admin=False)
        db.session.add_all([admin, regular])
        db.session.commit()

        admin_adult = Profile(user_id=admin.id, name="Admin Adult", is_child=False)
        admin_kid = Profile(user_id=admin.id, name="Admin Kid", is_child=True)
        regular_adult = Profile(
            user_id=regular.id, name="Regular Adult", is_child=False
        )
        db.session.add_all([admin_adult, admin_kid, regular_adult])
        db.session.commit()

        # Admin checks
        assert authorization.can(admin, "admin") is True
        assert authorization.can(admin, "metadata.edit") is True
        assert authorization.can(admin_adult, "admin") is True
        assert authorization.can(admin_kid, "admin") is False  # Child cannot admin
        assert authorization.can(regular, "admin") is False
        assert authorization.can(regular_adult, "admin") is False


def test_authorization_service_library_acls(app):
    """Verify granular library ACLs on read and download."""
    with app.app_context():
        user = User(username="reader_user")
        db.session.add(user)
        db.session.commit()

        prof = Profile(user_id=user.id, name="Restricted Reader", is_child=False)
        db.session.add(prof)

        lib_books = Library(
            slug="books", name="Books", path="/tmp/books", media_type="book"
        )
        lib_movies = Library(
            slug="movies", name="Movies", path="/tmp/movies", media_type="video"
        )
        db.session.add_all([lib_books, lib_movies])
        db.session.commit()

        # Grant full access to books, forbid movies
        acl_books = ProfileLibraryAccess(
            profile_id=prof.id,
            library_id=lib_books.id,
            can_read=True,
            can_download=True,
        )
        acl_movies = ProfileLibraryAccess(
            profile_id=prof.id,
            library_id=lib_movies.id,
            can_read=False,
            can_download=False,
        )
        db.session.add_all([acl_books, acl_movies])
        db.session.commit()

        # Check library.read
        assert authorization.can(prof, "library.read", lib_books) is True
        assert authorization.can(prof, "library.read", lib_movies) is False

        # Check library.download
        assert authorization.can(prof, "library.download", lib_books) is True
        assert authorization.can(prof, "library.download", lib_movies) is False

        # Check media.stream inheriting library ACL
        item_book = MediaItem(
            original_file_path="/tmp/books/b1.epub",
            title="Book 1",
            library_id=lib_books.id,
            file_hash="hash_b1",
            file_format="epub",
        )
        item_movie = MediaItem(
            original_file_path="/tmp/movies/m1.mp4",
            title="Movie 1",
            library_id=lib_movies.id,
            file_hash="hash_m1",
            file_format="mp4",
        )
        db.session.add_all([item_book, item_movie])
        db.session.commit()

        assert authorization.can(prof, "media.stream", item_book) is True
        assert authorization.can(prof, "media.stream", item_movie) is False


def test_authorization_service_child_safety_guard(app):
    """Verify child profile content restrictions on mature/explicit tags and downloads."""
    with app.app_context():
        user = User(username="parent_user")
        db.session.add(user)
        db.session.commit()

        child_prof = Profile(user_id=user.id, name="Child Profile", is_child=True)
        adult_prof = Profile(user_id=user.id, name="Adult Profile", is_child=False)
        db.session.add_all([child_prof, adult_prof])

        lib = Library(
            slug="comics", name="Comics", path="/tmp/comics", media_type="comic"
        )
        db.session.add(lib)
        db.session.commit()

        # Item with explicit tag
        mature_tag = Tag(name="explicit")
        db.session.add(mature_tag)

        mature_item = MediaItem(
            original_file_path="/tmp/comics/mature.cbz",
            title="Mature Comic",
            library_id=lib.id,
            file_hash="hash_mature",
            file_format="cbz",
            tags=[mature_tag],
        )
        safe_item = MediaItem(
            original_file_path="/tmp/comics/safe.cbz",
            title="Safe Comic",
            library_id=lib.id,
            file_hash="hash_safe",
            file_format="cbz",
        )
        db.session.add_all([mature_item, safe_item])
        db.session.commit()

        # Child cannot stream mature item
        assert authorization.can(child_prof, "media.stream", mature_item) is False
        assert authorization.can(adult_prof, "media.stream", mature_item) is True

        # Child can stream safe item
        assert authorization.can(child_prof, "media.stream", safe_item) is True

        # Child cannot download by default without explicit grant
        assert authorization.can(child_prof, "library.download", lib) is False
        assert authorization.can(adult_prof, "library.download", lib) is True


def test_api_profile_crud_endpoints(client, app, default_user):
    """Verify Profile REST API CRUD operations."""
    # 1. Create Profile
    res = client.post(
        "/api/profiles",
        json={
            "name": "Alice",
            "is_child": False,
            "avatar_url": "https://avatar.com/1.png",
        },
    )
    assert res.status_code == 201
    data = res.get_json()
    alice_id = data["profile"]["id"]
    assert data["profile"]["name"] == "Alice"

    # 2. List Profiles
    res = client.get("/api/profiles")
    assert res.status_code == 200
    profiles = res.get_json()["profiles"]
    assert any(p["id"] == alice_id for p in profiles)

    # 3. Get Profile
    res = client.get(f"/api/profiles/{alice_id}")
    assert res.status_code == 200
    assert res.get_json()["profile"]["name"] == "Alice"

    # 4. Update Profile
    res = client.put(f"/api/profiles/{alice_id}", json={"name": "Alice Wonder"})
    assert res.status_code == 200
    assert res.get_json()["profile"]["name"] == "Alice Wonder"

    # 5. Select Profile in session
    res = client.post(f"/api/profiles/{alice_id}/select")
    assert res.status_code == 200
    assert res.get_json()["active_profile"]["id"] == alice_id

    # 6. Delete Profile
    res = client.delete(f"/api/profiles/{alice_id}")
    assert res.status_code == 200
    assert "deleted" in res.get_json()["message"]


def test_api_profile_acl_enforcement(client, app, default_user, sample_epub):
    """Verify HTTP 403 Forbidden is enforced on media endpoints when profile ACL denies access."""
    with app.app_context():
        # Setup restricted library
        lib_restricted = Library(
            slug="classified",
            name="Classified",
            path=str(sample_epub.parent),
            media_type="book",
        )
        db.session.add(lib_restricted)
        db.session.commit()

        # Setup restricted item
        item = MediaItem(
            original_file_path=str(sample_epub.resolve()),
            title="Classified Docs",
            library_id=lib_restricted.id,
            file_format="epub",
            file_hash="hash_classified",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

        # Create restricted profile
        prof = Profile(user_id=default_user, name="Guest Profile", is_child=False)
        db.session.add(prof)
        db.session.commit()

        # Deny read & download
        acl = ProfileLibraryAccess(
            profile_id=prof.id,
            library_id=lib_restricted.id,
            can_read=False,
            can_download=False,
        )
        db.session.add(acl)
        db.session.commit()
        prof_id = prof.id

    headers = {"X-Aarkib-Profile-Id": str(prof_id)}

    # Attempt to view details -> 403
    res = client.get(f"/api/media/{item_id}", headers=headers)
    assert res.status_code == 403
    assert "Access denied" in res.get_json()["error"]

    # Attempt to stream -> 403
    res = client.get(f"/api/media/{item_id}/stream", headers=headers)
    assert res.status_code == 403

    # Attempt to download -> 403
    res = client.get(f"/api/media/{item_id}/download", headers=headers)
    assert res.status_code == 403
