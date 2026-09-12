from __future__ import annotations

from aarkib.extensions import db
from aarkib.models import (
    MediaItem,
    MediaType,
    User,
)


def test_user_favorites_model_and_api(client, app):
    with app.app_context():
        user = User(username="reader1")
        user.set_password("pass123")
        db.session.add(user)
        db.session.commit()

        item = MediaItem(
            title="Dune",
            original_file_path="/media/dune.epub",
            file_format="epub",
            file_hash="hash_dune",
            media_type=MediaType.BOOK.value,
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    # Unauthenticated attempt
    client.get("/auth/logout")
    res_unauth = client.post(f"/api/media/{item_id}/favorite")
    assert res_unauth.status_code == 401

    # Log in
    client.post(
        "/auth/login",
        data={"username": "reader1", "password": "pass123"},
        follow_redirects=True,
    )

    # 1. Star item
    res_star = client.post(f"/api/media/{item_id}/favorite", json={"favorite": True})
    assert res_star.status_code == 200
    assert res_star.json["favorited"] is True

    # 2. List favorites
    res_list = client.get("/api/favorites")
    assert res_list.status_code == 200
    assert res_list.json["count"] == 1
    assert res_list.json["favorites"][0]["title"] == "Dune"

    # 3. Toggle off
    res_toggle = client.post(f"/api/media/{item_id}/favorite")
    assert res_toggle.status_code == 200
    assert res_toggle.json["favorited"] is False

    # 4. List favorites again
    res_list2 = client.get("/api/favorites")
    assert res_list2.json["count"] == 0


def test_playlists_model_and_api(client, app):
    with app.app_context():
        user = User(username="curator1")
        user.set_password("curatorpass")
        db.session.add(user)
        db.session.commit()

        track1 = MediaItem(
            title="Track One",
            original_file_path="/music/t1.mp3",
            file_format="mp3",
            file_hash="h1",
            media_type=MediaType.MUSIC.value,
        )
        track2 = MediaItem(
            title="Track Two",
            original_file_path="/music/t2.mp3",
            file_format="mp3",
            file_hash="h2",
            media_type=MediaType.MUSIC.value,
        )
        db.session.add_all([track1, track2])
        db.session.commit()
        t1_id = track1.id
        t2_id = track2.id

    client.post(
        "/auth/login",
        data={"username": "curator1", "password": "curatorpass"},
        follow_redirects=True,
    )

    # 1. Create playlist
    res_create = client.post(
        "/api/playlists",
        json={
            "title": "Evening Chill",
            "description": "Relaxing ambient tracks",
            "media_type": "music",
            "is_public": True,
        },
    )
    assert res_create.status_code == 201
    playlist_id = res_create.json["playlist"]["id"]
    assert res_create.json["playlist"]["title"] == "Evening Chill"

    # 2. Add track 1
    res_add1 = client.post(
        f"/api/playlists/{playlist_id}/items",
        json={"media_item_id": t1_id},
    )
    assert res_add1.status_code == 201
    item1_entry_id = res_add1.json["item"]["id"]

    # 3. Add track 2
    res_add2 = client.post(
        f"/api/playlists/{playlist_id}/items",
        json={"media_item_id": t2_id},
    )
    assert res_add2.status_code == 201
    item2_entry_id = res_add2.json["item"]["id"]

    # 4. Get playlist details
    res_get = client.get(f"/api/playlists/{playlist_id}")
    assert res_get.status_code == 200
    assert len(res_get.json["playlist"]["items"]) == 2
    assert res_get.json["playlist"]["items"][0]["media_item"]["title"] == "Track One"

    # 5. Reorder: put track 2 first
    res_reorder = client.put(
        f"/api/playlists/{playlist_id}/reorder",
        json={"item_ids": [item2_entry_id, item1_entry_id]},
    )
    assert res_reorder.status_code == 200

    # Verify reordered
    res_get_reordered = client.get(f"/api/playlists/{playlist_id}")
    assert (
        res_get_reordered.json["playlist"]["items"][0]["media_item"]["title"]
        == "Track Two"
    )

    # 6. Remove item
    res_del_item = client.delete(f"/api/playlists/{playlist_id}/items/{item1_entry_id}")
    assert res_del_item.status_code == 200

    res_get_after_del = client.get(f"/api/playlists/{playlist_id}")
    assert len(res_get_after_del.json["playlist"]["items"]) == 1

    # 7. Delete playlist
    res_del_pl = client.delete(f"/api/playlists/{playlist_id}")
    assert res_del_pl.status_code == 200

    res_get_gone = client.get(f"/api/playlists/{playlist_id}")
    assert res_get_gone.status_code == 404


def test_playlist_authorization_guards(client, app):
    with app.app_context():
        user1 = User(username="user1")
        user1.set_password("pass1")
        user2 = User(username="user2")
        user2.set_password("pass2")
        db.session.add_all([user1, user2])
        db.session.commit()

        track = MediaItem(
            title="Song",
            original_file_path="/music/song.mp3",
            file_format="mp3",
            file_hash="h_song",
            media_type=MediaType.MUSIC.value,
        )
        db.session.add(track)
        db.session.commit()
        track_id = track.id

    # Log in as user1 and create a private playlist
    client.post(
        "/auth/login",
        data={"username": "user1", "password": "pass1"},
        follow_redirects=True,
    )
    res_create = client.post(
        "/api/playlists",
        json={"title": "User1 Private", "is_public": False},
    )
    assert res_create.status_code == 201
    playlist_id = res_create.json["playlist"]["id"]

    # Log out user1
    client.get("/auth/logout", follow_redirects=True)

    # Unauthenticated user should not be able to delete or modify user1's playlist
    res_unauth_del = client.delete(f"/api/playlists/{playlist_id}")
    assert res_unauth_del.status_code in (401, 403)

    res_unauth_add = client.post(
        f"/api/playlists/{playlist_id}/items",
        json={"media_item_id": track_id},
    )
    assert res_unauth_add.status_code in (401, 403)

    # Log in as user2
    client.post(
        "/auth/login",
        data={"username": "user2", "password": "pass2"},
        follow_redirects=True,
    )

    # User2 should not be able to delete or modify user1's playlist
    res_u2_del = client.delete(f"/api/playlists/{playlist_id}")
    assert res_u2_del.status_code == 403

    res_u2_add = client.post(
        f"/api/playlists/{playlist_id}/items",
        json={"media_item_id": track_id},
    )
    assert res_u2_add.status_code == 403

    # User2 should not be able to read user1's private playlist
    res_u2_get = client.get(f"/api/playlists/{playlist_id}")
    assert res_u2_get.status_code == 403
