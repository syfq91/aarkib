from __future__ import annotations

from pathlib import Path

from aarkib.extensions import db
from aarkib.models import Author, Collection, Library, MediaItem, User
from aarkib.plugins.jellyfin import (
    from_jellyfin_id,
    generate_jellyfin_token,
    to_jellyfin_id,
    verify_jellyfin_token,
)


def _setup_jellyfin_data(app, tmp_path: Path):
    with app.app_context():
        # User
        user = User(username="jf_user", is_admin=True)
        user.set_password("jf_pass")
        db.session.add(user)

        # Video Library & Movie
        movie_file = tmp_path / "Inception.mp4"
        movie_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"A" * 1024)

        video_lib = Library(
            name="Movies",
            slug="movies-lib",
            path=str(tmp_path),
            media_type="video",
        )
        db.session.add(video_lib)
        db.session.flush()

        movie = MediaItem(
            title="Inception",
            original_file_path=str(movie_file),
            file_format="mp4",
            file_hash="hash_jf_movie",
            file_size=len(movie_file.read_bytes()),
            duration=8880.0,
            resolution_width=1920,
            resolution_height=1080,
            codec="h264",
            release_year="2010",
            description="A thief who steals corporate secrets through dream-sharing technology.",
            library_id=video_lib.id,
            media_type="video",
        )
        db.session.add(movie)

        # TV Show Collection & Episode
        tv_col = Collection(name="Breaking Bad")
        db.session.add(tv_col)
        db.session.flush()

        ep_file = tmp_path / "BB_S01E01.mp4"
        ep_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"B" * 512)

        episode = MediaItem(
            title="Pilot",
            original_file_path=str(ep_file),
            file_format="mp4",
            file_hash="hash_jf_episode",
            file_size=len(ep_file.read_bytes()),
            duration=3480.0,
            season=1,
            episode=1,
            resolution_width=1920,
            resolution_height=1080,
            codec="h264",
            collection_id=tv_col.id,
            library_id=video_lib.id,
            media_type="video",
        )
        db.session.add(episode)

        # Audio Library, Artist, and Song
        music_lib = Library(
            name="Music",
            slug="music-lib",
            path=str(tmp_path / "music"),
            media_type="music",
        )
        db.session.add(music_lib)
        db.session.flush()

        artist = Author(name="Daft Punk")
        db.session.add(artist)
        db.session.flush()

        song_file = tmp_path / "Get_Lucky.mp3"
        song_file.write_bytes(b"ID3" + b"C" * 256)

        song = MediaItem(
            title="Get Lucky",
            original_file_path=str(song_file),
            file_format="mp3",
            file_hash="hash_jf_song",
            file_size=len(song_file.read_bytes()),
            duration=248.0,
            bitrate=320,
            track_number=8,
            library_id=music_lib.id,
            media_type="music",
            genre="Electronic",
        )
        song.creators = [artist]
        db.session.add(song)
        db.session.commit()

        return {
            "user_id": user.id,
            "video_lib_id": video_lib.id,
            "movie_id": movie.id,
            "tv_col_id": tv_col.id,
            "episode_id": episode.id,
            "music_lib_id": music_lib.id,
            "artist_id": artist.id,
            "song_id": song.id,
        }


def test_jellyfin_id_conversion():
    int_id = 42
    hex_id = to_jellyfin_id(int_id)
    assert len(hex_id) == 32
    assert hex_id == "0000000000000000000000000000002a"
    assert from_jellyfin_id(hex_id) == 42
    assert from_jellyfin_id("00000000-0000-0000-0000-00000000002a") == 42
    assert from_jellyfin_id("42") == 42
    assert from_jellyfin_id(None) is None


def test_jellyfin_token_lifecycle(app):
    with app.app_context():
        user = User(username="token_test_user")
        db.session.add(user)
        db.session.commit()

        token = generate_jellyfin_token(user.id)
        assert isinstance(token, str)
        assert len(token.split(".")) == 3

        verified = verify_jellyfin_token(token)
        assert verified is not None
        assert verified.id == user.id

        # Tampered token
        tampered = token + "bad"
        assert verify_jellyfin_token(tampered) is None


def test_jellyfin_system_discovery(app, client, tmp_path):
    _setup_jellyfin_data(app, tmp_path)

    # Public discovery (no auth required)
    resp = client.get("/System/Info/Public")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ServerName"] == "Aarkib"
    assert data["Version"] == "10.9.11"
    assert data["StartupWizardCompleted"] is True
    assert "Id" in data

    # Case-insensitivity test
    resp_lower = client.get("/system/info/public")
    assert resp_lower.status_code == 200
    assert resp_lower.get_json()["ServerName"] == "Aarkib"

    # Configuration & Endpoint
    resp_cfg = client.get("/System/Configuration")
    assert resp_cfg.status_code == 200
    assert resp_cfg.get_json()["IsStartupWizardCompleted"] is True

    resp_ep = client.get("/System/Endpoint")
    assert resp_ep.status_code == 200
    assert resp_ep.get_json()["IsLocal"] is True


def test_jellyfin_authentication_flow(app, client, tmp_path):
    _setup_jellyfin_data(app, tmp_path)

    # Missing / Invalid credentials
    resp_bad = client.post(
        "/Users/AuthenticateByName",
        json={"Username": "jf_user", "Pw": "wrongpass"},
    )
    assert resp_bad.status_code == 401

    # Valid credentials with MediaBrowser header
    headers = {
        "X-Emby-Authorization": 'MediaBrowser Client="Jellyfin Mobile", Device="Pixel 7", DeviceId="testdev123", Version="2.6.0"'
    }
    resp = client.post(
        "/Users/AuthenticateByName",
        json={"Username": "jf_user", "Pw": "jf_pass"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "AccessToken" in data
    assert data["User"]["Name"] == "jf_user"
    assert data["User"]["Policy"]["IsAdministrator"] is True
    assert data["SessionInfo"]["Client"] == "Jellyfin Mobile"
    assert data["SessionInfo"]["DeviceName"] == "Pixel 7"

    token = data["AccessToken"]

    # Authenticated request with X-Emby-Token header
    resp_info = client.get("/System/Info", headers={"X-Emby-Token": token})
    assert resp_info.status_code == 200
    assert resp_info.get_json()["ServerName"] == "Aarkib"

    # Authenticated request with Authorization header
    resp_auth_hdr = client.get(
        "/System/Info",
        headers={"Authorization": f'MediaBrowser Token="{token}"'},
    )
    assert resp_auth_hdr.status_code == 200

    # Authenticated request with query parameter api_key
    resp_query = client.get(f"/System/Info?api_key={token}")
    assert resp_query.status_code == 200


def test_jellyfin_users_and_views(app, client, tmp_path):
    ids = _setup_jellyfin_data(app, tmp_path)

    # Login to obtain token
    resp_auth = client.post(
        "/Users/AuthenticateByName",
        json={"Username": "jf_user", "Pw": "jf_pass"},
    )
    token = resp_auth.get_json()["AccessToken"]
    user_id = to_jellyfin_id(ids["user_id"])
    headers = {"X-Emby-Token": token}

    # Public users
    resp_pub = client.get("/Users/Public")
    assert resp_pub.status_code == 200
    assert len(resp_pub.get_json()) >= 1

    # User by ID and Me
    resp_user = client.get(f"/Users/{user_id}", headers=headers)
    assert resp_user.status_code == 200
    assert resp_user.get_json()["Name"] == "jf_user"

    resp_me = client.get("/Users/Me", headers=headers)
    assert resp_me.status_code == 200
    assert resp_me.get_json()["Name"] == "jf_user"

    # Views (Libraries)
    resp_views = client.get(f"/Users/{user_id}/Views", headers=headers)
    assert resp_views.status_code == 200
    views_data = resp_views.get_json()
    assert views_data["TotalRecordCount"] >= 2
    types = [v["CollectionType"] for v in views_data["Items"]]
    assert "movies" in types
    assert "music" in types


def test_jellyfin_catalog_and_items(app, client, tmp_path):
    ids = _setup_jellyfin_data(app, tmp_path)

    resp_auth = client.post(
        "/Users/AuthenticateByName",
        json={"Username": "jf_user", "Pw": "jf_pass"},
    )
    token = resp_auth.get_json()["AccessToken"]
    headers = {"X-Emby-Token": token}
    user_id = to_jellyfin_id(ids["user_id"])

    # Browse all items
    resp_items = client.get(f"/Users/{user_id}/Items", headers=headers)
    assert resp_items.status_code == 200
    items_data = resp_items.get_json()
    assert items_data["TotalRecordCount"] >= 3

    # Filter by ParentId (Movies library)
    vid_lib_hex = to_jellyfin_id(ids["video_lib_id"])
    resp_vid_items = client.get(f"/Items?ParentId={vid_lib_hex}", headers=headers)
    assert resp_vid_items.status_code == 200
    vid_data = resp_vid_items.get_json()
    titles = [i["Name"] for i in vid_data["Items"]]
    assert "Inception" in titles
    assert "Pilot" in titles

    # Filter by ItemTypes=Movie
    resp_movies = client.get("/Items?IncludeItemTypes=Movie", headers=headers)
    assert resp_movies.status_code == 200
    movies_data = resp_movies.get_json()
    assert any(i["Name"] == "Inception" for i in movies_data["Items"])

    # Filter by ItemTypes=Episode
    resp_eps = client.get("/Items?IncludeItemTypes=Episode", headers=headers)
    assert resp_eps.status_code == 200
    eps_data = resp_eps.get_json()
    assert any(i["Name"] == "Pilot" for i in eps_data["Items"])

    # Item detail
    movie_hex = to_jellyfin_id(ids["movie_id"])
    resp_detail = client.get(f"/Items/{movie_hex}", headers=headers)
    assert resp_detail.status_code == 200
    detail = resp_detail.get_json()
    assert detail["Name"] == "Inception"
    assert detail["Type"] == "Movie"
    assert detail["MediaType"] == "Video"
    assert detail["ProductionYear"] == 2010
    assert len(detail["MediaStreams"]) >= 2

    # Series Seasons and Episodes
    series_hex = to_jellyfin_id(ids["tv_col_id"])
    resp_seasons = client.get(f"/Shows/{series_hex}/Seasons", headers=headers)
    assert resp_seasons.status_code == 200
    assert resp_seasons.get_json()["TotalRecordCount"] == 1

    resp_series_eps = client.get(f"/Shows/{series_hex}/Episodes", headers=headers)
    assert resp_series_eps.status_code == 200
    assert resp_series_eps.get_json()["TotalRecordCount"] == 1
    assert resp_series_eps.get_json()["Items"][0]["Name"] == "Pilot"


def test_jellyfin_playback_info_and_streaming(app, client, tmp_path):
    ids = _setup_jellyfin_data(app, tmp_path)

    resp_auth = client.post(
        "/Users/AuthenticateByName",
        json={"Username": "jf_user", "Pw": "jf_pass"},
    )
    token = resp_auth.get_json()["AccessToken"]
    headers = {"X-Emby-Token": token}

    movie_hex = to_jellyfin_id(ids["movie_id"])

    # PlaybackInfo
    resp_pb = client.post(f"/Items/{movie_hex}/PlaybackInfo", headers=headers)
    assert resp_pb.status_code == 200
    pb_data = resp_pb.get_json()
    assert "MediaSources" in pb_data
    assert len(pb_data["MediaSources"]) == 1
    source = pb_data["MediaSources"][0]
    assert source["SupportsDirectPlay"] is True
    assert f"/Videos/{movie_hex}/stream" in source["DirectStreamUrl"]

    # Stream video with byte range request
    resp_stream = client.get(
        f"/Videos/{movie_hex}/stream",
        headers={"Range": "bytes=0-15"},
    )
    assert resp_stream.status_code == 206
    assert resp_stream.data == b"\x00\x00\x00\x20ftypisomAAAA"

    # Stream audio
    song_hex = to_jellyfin_id(ids["song_id"])
    resp_audio = client.get(f"/Audio/{song_hex}/stream")
    assert resp_audio.status_code in (200, 206)
    assert resp_audio.content_type.startswith("audio/")


def test_jellyfin_progress_scrobbling_and_played(app, client, tmp_path):
    ids = _setup_jellyfin_data(app, tmp_path)

    resp_auth = client.post(
        "/Users/AuthenticateByName",
        json={"Username": "jf_user", "Pw": "jf_pass"},
    )
    token = resp_auth.get_json()["AccessToken"]
    headers = {"X-Emby-Token": token}
    movie_hex = to_jellyfin_id(ids["movie_id"])
    user_hex = to_jellyfin_id(ids["user_id"])

    # 1. Playback started
    resp_start = client.post(
        "/Sessions/Playing",
        json={"ItemId": movie_hex},
        headers=headers,
    )
    assert resp_start.status_code == 204

    # 2. Progress update: 120 seconds into movie (120 * 10,000,000 = 1,200,000,000 ticks)
    resp_prog = client.post(
        "/Sessions/Playing/Progress",
        json={"ItemId": movie_hex, "PositionTicks": 1_200_000_000},
        headers=headers,
    )
    assert resp_prog.status_code == 204

    # Check Resume list
    resp_resume = client.get(f"/Users/{user_hex}/Items/Resume", headers=headers)
    assert resp_resume.status_code == 200
    res_items = resp_resume.get_json()["Items"]
    assert len(res_items) >= 1
    assert res_items[0]["Name"] == "Inception"
    assert res_items[0]["UserData"]["PlaybackPositionTicks"] == 1_200_000_000
    assert res_items[0]["UserData"]["Played"] is False

    # 3. Mark played
    resp_played = client.post(
        f"/Users/{user_hex}/PlayedItems/{movie_hex}", headers=headers
    )
    assert resp_played.status_code == 200
    assert resp_played.get_json()["UserData"]["Played"] is True

    # 4. Mark unplayed
    resp_unplayed = client.delete(
        f"/Users/{user_hex}/PlayedItems/{movie_hex}", headers=headers
    )
    assert resp_unplayed.status_code == 200
    assert resp_unplayed.get_json()["UserData"]["Played"] is False


def test_jellyfin_runtime_settings_guard(app, client, tmp_path):
    from aarkib.services.settings_service import update_settings

    _setup_jellyfin_data(app, tmp_path)

    # Disable Jellyfin plugin
    update_settings(app, {"ENABLE_JELLYFIN": False})

    resp = client.get("/System/Info/Public")
    assert resp.status_code == 503
    assert b"disabled" in resp.data.lower()

    # Re-enable Jellyfin plugin
    update_settings(app, {"ENABLE_JELLYFIN": True})

    resp_enabled = client.get("/System/Info/Public")
    assert resp_enabled.status_code == 200
    assert resp_enabled.get_json()["ServerName"] == "Aarkib"


def test_jellyfin_stream_auth_enforced(app, tmp_path):
    """Verify that streaming endpoints strictly require authentication (H2)."""
    ids = _setup_jellyfin_data(app, tmp_path)
    movie_hex = to_jellyfin_id(ids["movie_id"])
    song_hex = to_jellyfin_id(ids["song_id"])

    # Use a fresh, unauthenticated test client
    unauth_client = app.test_client()

    # 1. Unauthenticated video stream attempt -> 401
    resp_unauth_video = unauth_client.get(f"/Videos/{movie_hex}/stream")
    assert resp_unauth_video.status_code == 401

    # 2. Unauthenticated audio stream attempt -> 401
    resp_unauth_audio = unauth_client.get(f"/Audio/{song_hex}/stream")
    assert resp_unauth_audio.status_code == 401

    # 3. Authenticate to obtain token
    resp_auth = unauth_client.post(
        "/Users/AuthenticateByName",
        json={"Username": "jf_user", "Pw": "jf_pass"},
    )
    assert resp_auth.status_code == 200
    token = resp_auth.get_json()["AccessToken"]

    # 4. Stream using another client with ?api_key query parameter
    clean_client = app.test_client()
    resp_query_stream = clean_client.get(f"/Videos/{movie_hex}/stream?api_key={token}")
    assert resp_query_stream.status_code in (200, 206)

    # 5. Stream using header X-Emby-Token
    resp_hdr_stream = clean_client.get(
        f"/Audio/{song_hex}/stream", headers={"X-Emby-Token": token}
    )
    assert resp_hdr_stream.status_code in (200, 206)
