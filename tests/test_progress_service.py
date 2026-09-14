from __future__ import annotations

import pytest

from aarkib.extensions import db
from aarkib.models import Book, User
from aarkib.services.progress_service import (
    add_bookmark,
    delete_bookmark,
    get_progress,
    get_progress_for_items,
    list_bookmarks,
    update_progress,
)


def test_progress_crud_and_metrics(app):
    with app.app_context():
        user = User(username="testuser", password_hash="dummy")
        db.session.add(user)
        item = Book(
            original_file_path="/media/book1.epub",
            title="Book 1",
            file_hash="hash1",
            file_format="epub",
            duration=300.0,
        )
        db.session.add(item)
        db.session.commit()

        # Initial default progress
        prog_initial = get_progress(user.id, item)
        assert prog_initial["percentage"] == 0.0
        assert prog_initial["location"] == "0"
        assert prog_initial["is_completed"] is False

        # Update progress
        update_progress(
            user.id,
            item,
            {
                "location": "epubcfi(/6/4)",
                "percentage": 45.5,
                "position_seconds": 120.0,
                "playback_speed": 1.25,
            },
        )

        prog_updated = get_progress(user.id, item)
        assert prog_updated["percentage"] == 45.5
        assert prog_updated["location"] == "epubcfi(/6/4)"
        assert prog_updated["position_seconds"] == 120.0
        assert prog_updated["playback_speed"] == 1.25
        assert prog_updated["is_completed"] is False

        # Multi-item progress mapping
        item2 = Book(
            original_file_path="/media/book2.epub",
            title="Book 2",
            file_hash="hash2",
            file_format="epub",
        )
        db.session.add(item2)
        db.session.commit()

        pmap = get_progress_for_items(user.id, [item.id, item2.id])
        assert item.id in pmap
        assert pmap[item.id]["percentage"] == 45.5
        assert item2.id not in pmap


def test_bookmark_lifecycle_and_permissions(app):
    with app.app_context():
        user1 = User(username="u1", password_hash="dummy")
        user2 = User(username="u2", password_hash="dummy")
        item = Book(
            original_file_path="/media/book3.epub",
            title="Book 3",
            file_hash="hash3",
            file_format="epub",
        )
        db.session.add_all([user1, user2, item])
        db.session.commit()

        # Add bookmark requires location
        with pytest.raises(ValueError, match="Location is required"):
            add_bookmark(item.id, user1.id, location="")

        bm = add_bookmark(
            item.id,
            user1.id,
            location="page-42",
            title="Important clue",
            snippet="The butler did it",
        )
        assert bm.id is not None
        assert bm.title == "Important clue"

        bms = list_bookmarks(item.id, user1.id)
        assert len(bms) == 1
        assert bms[0].id == bm.id

        # User2 forbidden from deleting user1's bookmark
        with pytest.raises(PermissionError):
            delete_bookmark(bm.id, user_id=user2.id)

        # Deleting non-existent bookmark raises KeyError
        with pytest.raises(KeyError):
            delete_bookmark(99999, user_id=user1.id)

        # User1 deletes own bookmark
        assert delete_bookmark(bm.id, user_id=user1.id) is True
        assert len(list_bookmarks(item.id, user1.id)) == 0
