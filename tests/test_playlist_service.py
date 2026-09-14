from __future__ import annotations

import pytest

from aarkib.extensions import db
from aarkib.models import MediaItem, User
from aarkib.services.playlist_service import (
    add_playlist_item,
    create_playlist,
    delete_playlist,
    get_playlist,
    list_favorites,
    list_playlists,
    remove_playlist_item,
    reorder_playlist_items,
    toggle_favorite,
)


def test_playlist_lifecycle_and_permissions(app):
    with app.app_context():
        user1 = User(username="p1", password_hash="dummy")
        user2 = User(username="p2", password_hash="dummy")
        item1 = MediaItem(
            original_file_path="/media/track1.mp3",
            title="Track 1",
            file_hash="thash1",
            file_format="mp3",
        )
        item2 = MediaItem(
            original_file_path="/media/track2.mp3",
            title="Track 2",
            file_hash="thash2",
            file_format="mp3",
        )
        db.session.add_all([user1, user2, item1, item2])
        db.session.commit()

        # Empty title fails
        with pytest.raises(ValueError, match="Playlist title is required"):
            create_playlist(user1.id, title="")

        # Create private playlist for user1
        pl = create_playlist(user1.id, title="My Tunes", is_public=False)
        assert pl.id is not None
        assert pl.title == "My Tunes"

        # List playlists
        pls = list_playlists(user1.id)
        assert any(p.id == pl.id for p in pls)

        # User2 cannot view private playlist
        with pytest.raises(PermissionError):
            get_playlist(pl.id, user_id=user2.id)

        # User2 cannot add items to user1's playlist
        with pytest.raises(PermissionError):
            add_playlist_item(pl.id, user_id=user2.id, media_item_id=item1.id)

        # User1 adds items
        pi1 = add_playlist_item(pl.id, user1.id, media_item_id=item1.id)
        pi2 = add_playlist_item(pl.id, user1.id, media_item_id=item2.id)
        assert pi1.media_item_id == item1.id
        assert pi2.media_item_id == item2.id

        # Reorder items
        reorder_playlist_items(pl.id, user1.id, [item2.id, item1.id])
        pl_refreshed = get_playlist(pl.id, user1.id)
        ordered_ids = [item.media_item_id for item in pl_refreshed.items]
        assert ordered_ids == [item2.id, item1.id]

        # Remove item
        assert remove_playlist_item(pl.id, user1.id, item1.id) is True
        pl_refreshed = get_playlist(pl.id, user1.id)
        assert len(pl_refreshed.items) == 1

        # Delete playlist
        with pytest.raises(PermissionError):
            delete_playlist(pl.id, user2.id)

        assert delete_playlist(pl.id, user1.id) is True
        with pytest.raises(KeyError):
            get_playlist(pl.id, user1.id)


def test_favorites_service(app):
    with app.app_context():
        user = User(username="favuser", password_hash="dummy")
        item = MediaItem(
            original_file_path="/media/fav.epub",
            title="Fav Book",
            file_hash="fhash",
            file_format="epub",
        )
        db.session.add_all([user, item])
        db.session.commit()

        # Initially not favorited
        favs = list_favorites(user.id)
        assert len(favs) == 0

        # Toggle on
        state = toggle_favorite(user.id, item.id)
        assert state is True
        favs = list_favorites(user.id)
        assert len(favs) == 1
        assert favs[0].media_item_id == item.id

        # Explicit set True again (idempotent)
        assert toggle_favorite(user.id, item.id, explicit_state=True) is True
        assert len(list_favorites(user.id)) == 1

        # Toggle off
        assert toggle_favorite(user.id, item.id) is False
        assert len(list_favorites(user.id)) == 0
