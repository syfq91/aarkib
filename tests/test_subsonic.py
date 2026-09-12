from __future__ import annotations

import base64

from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import Author, Collection, Library, MediaItem, User, UserProgress


def _setup_subsonic_data(app):
    with app.app_context():
        # Admin user
        user = User(username="sub_user", is_admin=False)
        user.set_password("sub_pass")
        db.session.add(user)

        # Library
        lib = Library(
            name="Music", slug="music-lib", path="/media/music", media_type="music"
        )
        db.session.add(lib)
        db.session.flush()

        # Artist & Album
        artist = Author(name="Pink Floyd")
        db.session.add(artist)
        db.session.flush()

        album = Collection(name="The Dark Side of the Moon")
        db.session.add(album)
        db.session.flush()

        # Song
        song = MediaItem(
            title="Time",
            original_file_path="/media/music/Pink Floyd/04 - Time.mp3",
            file_format="mp3",
            file_hash="hash_sub_time",
            file_size=8388608,
            duration=413.0,
            bitrate=320,
            track_number=4,
            collection_id=album.id,
            library_id=lib.id,
            media_type="music",
        )
        song.creators = [artist]
        db.session.add(song)
        db.session.commit()

        return {
            "user_id": user.id,
            "artist_id": artist.id,
            "album_id": album.id,
            "song_id": song.id,
        }


def test_subsonic_ping_and_license(app, client):
    _setup_subsonic_data(app)

    # Ping with valid credentials
    resp = client.get("/rest/ping.view?u=sub_user&p=sub_pass&v=1.16.1&c=testapp&f=json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "subsonic-response" in data
    assert data["subsonic-response"]["status"] == "ok"
    assert data["subsonic-response"]["openSubsonic"] is True

    # License check
    resp2 = client.get("/rest/getLicense.view?u=sub_user&p=sub_pass&f=json")
    assert resp2.status_code == 200
    lic_data = resp2.get_json()["subsonic-response"]
    assert lic_data["status"] == "ok"
    assert lic_data["license"]["valid"] is True


def test_subsonic_auth_failures_and_basic_auth(app, client):
    _setup_subsonic_data(app)

    # Wrong password
    resp = client.get("/rest/ping.view?u=sub_user&p=wrongpass&f=json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["subsonic-response"]["status"] == "failed"
    assert data["subsonic-response"]["error"]["code"] == 40

    # HTTP Basic Auth success
    creds = base64.b64encode(b"sub_user:sub_pass").decode("utf-8")
    resp_basic = client.get(
        "/rest/ping.view?f=json",
        headers={"Authorization": f"Basic {creds}"},
    )
    assert resp_basic.status_code == 200
    assert resp_basic.get_json()["subsonic-response"]["status"] == "ok"


def test_subsonic_music_folders_and_catalog_hierarchy(app, client):
    ids = _setup_subsonic_data(app)
    auth_q = "u=sub_user&p=sub_pass&f=json"

    # Music Folders
    res_folders = client.get(f"/rest/getMusicFolders.view?{auth_q}")
    assert res_folders.status_code == 200
    f_data = res_folders.get_json()["subsonic-response"]
    folders = f_data["musicFolders"]["musicFolder"]
    assert any(f["name"] == "Music" for f in folders)

    # Artists
    res_artists = client.get(f"/rest/getArtists.view?{auth_q}")
    assert res_artists.status_code == 200
    a_data = res_artists.get_json()["subsonic-response"]
    indexes = a_data["artists"]["index"]
    assert any(idx["name"] == "P" for idx in indexes)

    # Specific Artist
    res_artist = client.get(f"/rest/getArtist.view?id={ids['artist_id']}&{auth_q}")
    assert res_artist.status_code == 200
    art_data = res_artist.get_json()["subsonic-response"]["artist"]
    assert art_data["name"] == "Pink Floyd"
    assert len(art_data["album"]) >= 1

    # Specific Album
    res_album = client.get(f"/rest/getAlbum.view?id={ids['album_id']}&{auth_q}")
    assert res_album.status_code == 200
    alb_data = res_album.get_json()["subsonic-response"]["album"]
    assert alb_data["name"] == "The Dark Side of the Moon"
    assert len(alb_data["song"]) == 1
    assert alb_data["song"][0]["title"] == "Time"

    # Specific Song
    res_song = client.get(f"/rest/getSong.view?id={ids['song_id']}&{auth_q}")
    assert res_song.status_code == 200
    song_data = res_song.get_json()["subsonic-response"]["song"]
    assert song_data["title"] == "Time"
    assert song_data["track"] == 4


def test_subsonic_search3(app, client):
    _setup_subsonic_data(app)
    auth_q = "u=sub_user&p=sub_pass&f=json"

    resp = client.get(f"/rest/search3.view?query=Pink&{auth_q}")
    assert resp.status_code == 200
    s_data = resp.get_json()["subsonic-response"]["searchResult3"]
    assert any(ar["name"] == "Pink Floyd" for ar in s_data.get("artist", []))

    resp_song = client.get(f"/rest/search3.view?query=Time&{auth_q}")
    assert resp_song.status_code == 200
    s_song_data = resp_song.get_json()["subsonic-response"]["searchResult3"]
    assert any(s["title"] == "Time" for s in s_song_data.get("song", []))


def test_subsonic_scrobble(app, client):
    ids = _setup_subsonic_data(app)
    auth_q = "u=sub_user&p=sub_pass&f=json"

    # Progress scrobble (not finished)
    resp = client.get(
        f"/rest/scrobble.view?id={ids['song_id']}&time=120000&submission=false&{auth_q}"
    )
    assert resp.status_code == 200
    assert resp.get_json()["subsonic-response"]["status"] == "ok"

    with app.app_context():
        prog = db.session.scalar(
            select(UserProgress).where(
                UserProgress.user_id == ids["user_id"],
                UserProgress.media_item_id == ids["song_id"],
            )
        )
        assert prog is not None
        assert float(prog.progress_location) == 120.0
        assert prog.is_completed is False

    # Submission scrobble (finished)
    resp2 = client.get(
        f"/rest/scrobble.view?id={ids['song_id']}&submission=true&{auth_q}"
    )
    assert resp2.status_code == 200

    with app.app_context():
        prog2 = db.session.scalar(
            select(UserProgress).where(
                UserProgress.user_id == ids["user_id"],
                UserProgress.media_item_id == ids["song_id"],
            )
        )
        assert prog2.is_completed is True
        assert prog2.percentage == 100.0


def test_subsonic_stream_and_cover_art(app, client, tmp_path):
    # Create actual dummy file on disk for streaming test
    music_dir = tmp_path / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    sample_file = music_dir / "sample.mp3"
    sample_file.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00#dummy_audio_bytes_data")

    with app.app_context():
        user = User(username="stream_user", is_admin=False)
        user.set_password("streampass")
        db.session.add(user)

        item = MediaItem(
            title="Stream Test Song",
            original_file_path=str(sample_file),
            file_format="mp3",
            file_hash="hash_stream_mp3",
            file_size=sample_file.stat().st_size,
            duration=3.0,
            media_type="music",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    auth_q = "u=stream_user&p=streampass"

    # Test stream
    resp_stream = client.get(f"/rest/stream.view?id={item_id}&{auth_q}")
    assert resp_stream.status_code == 200
    assert resp_stream.mimetype == "audio/mpeg"
    assert b"dummy_audio_bytes" in resp_stream.data
