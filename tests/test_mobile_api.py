from pathlib import Path

from aarkib.extensions import db
from aarkib.models import (
    Collection,
    Creator,
    MediaItem,
    Tag,
    User,
    UserFavorite,
    UserProgress,
)
from aarkib.services.indexer import index_media_file


def _create_user(
    app, username="mobile_user", password="secretpassword", is_admin=False
):
    with app.app_context():
        user = User(username=username, is_admin=is_admin)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def test_api_auth_login(app, unauth_client):
    user_id = _create_user(app, username="tv_viewer", password="password123")

    # Missing fields
    res = unauth_client.post("/api/auth/login", json={})
    assert res.status_code == 400

    # Wrong password
    res = unauth_client.post(
        "/api/auth/login", json={"username": "tv_viewer", "password": "wrong"}
    )
    assert res.status_code == 401

    # Successful login
    res = unauth_client.post(
        "/api/auth/login",
        json={
            "username": "tv_viewer",
            "password": "password123",
            "device_name": "Living Room TV",
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert "token" in data
    assert data["token"].startswith("ark_")
    assert data["token_type"] == "Bearer"
    assert data["user"]["id"] == user_id
    assert data["user"]["username"] == "tv_viewer"

    # Use token to access authenticated API
    token = data["token"]
    res_auth = unauth_client.get(
        "/api/home",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_auth.status_code == 200


def test_device_code_pairing_flow(app, client, unauth_client, default_user):
    # 1. TV client requests a pairing code
    res = unauth_client.post(
        "/api/auth/device-code",
        json={"device_name": "Bedroom Apple TV", "client_id": "appletv-client-1"},
    )
    assert res.status_code == 200
    pair_data = res.get_json()
    assert "device_code" in pair_data
    assert "user_code" in pair_data
    assert "verification_uri" in pair_data
    assert len(pair_data["user_code"]) == 7  # e.g. ABC-XYZ or ABC-123
    device_code = pair_data["device_code"]
    user_code = pair_data["user_code"]

    # 2. TV polls before authorization -> authorization_pending
    res = unauth_client.post(
        "/api/auth/device-code/token",
        json={"device_code": device_code},
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "authorization_pending"

    # 3. User navigates to /pair in web browser
    res = client.get("/pair")
    assert res.status_code == 200
    assert b"Pair TV Device" in res.data

    # Submit invalid code
    res = client.post("/pair", data={"user_code": "INVALID"})
    assert res.status_code == 400

    # Submit valid user code
    res = client.post("/pair", data={"user_code": user_code})
    assert res.status_code == 200
    assert b"Device authorized successfully" in res.data

    # 4. TV polls after authorization -> gets bearer token
    res = unauth_client.post(
        "/api/auth/device-code/token",
        json={"device_code": device_code},
    )
    assert res.status_code == 200
    poll_data = res.get_json()
    assert poll_data["status"] == "success"
    assert poll_data["token_type"] == "Bearer"
    token = poll_data["access_token"]
    assert token.startswith("ark_")
    assert poll_data["user"]["id"] == default_user

    # 5. Subsequent poll fails (code consumed)
    res = unauth_client.post(
        "/api/auth/device-code/token",
        json={"device_code": device_code},
    )
    assert res.status_code == 400


def test_home_feed_and_in_progress(app, client, default_user, sample_epub):
    # Index media item
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

        # Mark favorite
        fav = UserFavorite(user_id=default_user, media_item_id=book_id)
        db.session.add(fav)

        # Mark in progress (50% read)
        prog = UserProgress(
            user_id=default_user,
            media_item_id=book_id,
            percentage=0.5,
            progress_location="chapter1.html",
        )
        db.session.add(prog)
        db.session.commit()

    # Home feed
    res = client.get("/api/home")
    assert res.status_code == 200
    data = res.get_json()
    assert "continue_reading" in data
    assert "continue_watching" in data
    assert "continue_listening" in data
    assert "next_up" in data
    assert "recently_added" in data
    assert "favorites" in data

    # Verify continue_reading has our book with progress
    assert len(data["continue_reading"]) == 1
    item = data["continue_reading"][0]
    assert item["id"] == book_id
    assert item["progress"]["progress"] == 0.5
    assert item["is_favorite"] is True

    # Verify favorites rail has the item
    assert len(data["favorites"]) == 1
    assert data["favorites"][0]["id"] == book_id

    # Verify /api/media?in_progress=true
    res = client.get("/api/media?in_progress=true")
    assert res.status_code == 200
    media_data = res.get_json()
    assert len(media_data["items"]) == 1
    assert media_data["items"][0]["id"] == book_id


def test_taxonomies_api(app, client, default_user):
    with app.app_context():
        creator = Creator(name="Isaac Asimov")
        coll = Collection(name="Foundation Series")
        tag = Tag(name="Science Fiction")
        db.session.add_all([creator, coll, tag])
        db.session.flush()

        item = MediaItem(
            title="Foundation and Empire",
            original_file_path=str(Path(app.config["MEDIA_DIR"]) / "foundation.epub"),
            file_format="epub",
            file_hash="dummyhash123",
            file_size=1024,
            media_type="book",
            collection_id=coll.id,
            series_index=2.0,
        )
        item.creators.append(creator)
        item.tags.append(tag)
        db.session.add(item)
        db.session.commit()
        creator_id = creator.id
        coll_id = coll.id

    # Test creators list & detail
    res = client.get("/api/creators")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["creators"]) >= 1
    found = [c for c in data["creators"] if c["id"] == creator_id]
    assert len(found) == 1
    assert found[0]["media_count"] == 1

    res = client.get(f"/api/creators/{creator_id}")
    assert res.status_code == 200
    creator_detail = res.get_json()
    assert creator_detail["name"] == "Isaac Asimov"
    assert len(creator_detail["items"]) == 1

    # Test collections list & detail
    res = client.get("/api/collections")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["collections"]) >= 1
    found = [c for c in data["collections"] if c["id"] == coll_id]
    assert len(found) == 1
    assert found[0]["media_count"] == 1

    res = client.get(f"/api/collections/{coll_id}")
    assert res.status_code == 200
    coll_detail = res.get_json()
    assert coll_detail["name"] == "Foundation Series"
    assert len(coll_detail["items"]) == 1
    assert coll_detail["items"][0]["series_index"] == 2.0

    # Test tags list
    res = client.get("/api/tags")
    assert res.status_code == 200
    tags_data = res.get_json()
    assert len(tags_data["tags"]) >= 1
    found_tag = [t for t in tags_data["tags"] if t["name"] == "Science Fiction"]
    assert len(found_tag) == 1
    assert found_tag[0]["media_count"] == 1


def test_openapi_spec_and_docs(client):
    # OpenAPI JSON endpoint
    res = client.get("/api/openapi.json")
    assert res.status_code == 200
    assert res.is_json
    spec = res.get_json()
    assert spec["openapi"] == "3.1.0"
    assert "/api/home" in spec["paths"]
    assert "/api/auth/login" in spec["paths"]
    assert "/api/auth/device-code" in spec["paths"]

    # Interactive Docs endpoint
    res = client.get("/api/docs")
    assert res.status_code == 200
    assert b"Aarkib API Reference & Interactive Explorer" in res.data


def test_device_code_expired(app, unauth_client):
    from datetime import UTC, datetime, timedelta

    from aarkib.models.token import DevicePairingCode

    res = unauth_client.post(
        "/api/auth/device-code",
        json={"device_name": "Temporary TV"},
    )
    assert res.status_code == 200
    device_code = res.get_json()["device_code"]

    # Expire it manually in DB
    with app.app_context():
        code_obj = db.session.scalar(
            db.select(DevicePairingCode).where(
                DevicePairingCode.device_code == device_code
            )
        )
        assert code_obj is not None
        code_obj.expires_at = datetime.now(UTC) - timedelta(seconds=10)
        db.session.commit()

    # Poll should report expired
    res = unauth_client.post(
        "/api/auth/device-code/token",
        json={"device_code": device_code},
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "expired_code"


def test_unauthenticated_api_home(unauth_client):
    res = unauth_client.get("/api/home")
    assert res.status_code == 401


def test_tv_next_up_aggregation(app, client, default_user):
    with app.app_context():
        show = Collection(name="The Expanse")
        db.session.add(show)
        db.session.flush()

        ep1 = MediaItem(
            title="Dulcinea",
            original_file_path=str(Path(app.config["MEDIA_DIR"]) / "s01e01.mp4"),
            file_format="mp4",
            file_hash="ep1hash",
            file_size=2048,
            media_type="video",
            collection_id=show.id,
            season=1,
            episode=1,
        )
        ep2 = MediaItem(
            title="The Big Empty",
            original_file_path=str(Path(app.config["MEDIA_DIR"]) / "s01e02.mp4"),
            file_format="mp4",
            file_hash="ep2hash",
            file_size=2048,
            media_type="video",
            collection_id=show.id,
            season=1,
            episode=2,
        )
        db.session.add_all([ep1, ep2])
        db.session.flush()

        # User watched Episode 1
        prog = UserProgress(
            user_id=default_user,
            media_item_id=ep1.id,
            percentage=100.0,
            is_completed=True,
        )
        db.session.add(prog)
        db.session.commit()
        ep2_id = ep2.id

    res = client.get("/api/home")
    assert res.status_code == 200
    feed = res.get_json()
    assert "next_up" in feed
    assert len(feed["next_up"]) >= 1
    next_item = feed["next_up"][0]
    assert next_item["id"] == ep2_id
    assert next_item["season"] == 1
    assert next_item["episode"] == 2
