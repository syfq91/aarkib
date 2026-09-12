from pathlib import Path

from aarkib.models import User
from aarkib.services.scanner import index_single_book


def _create_admin(app):
    """Create an admin user for tests requiring authenticated admin access."""
    admin = User(username="test_admin", is_admin=True)
    admin.set_password("adminpass")
    with app.app_context():
        from aarkib.extensions import db

        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id
    return admin_id


def _login_admin(client, app):
    _create_admin(app)
    client.post(
        "/auth/login",
        data={"username": "test_admin", "password": "adminpass"},
        follow_redirects=True,
    )


def test_api_books_and_progress(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    # List media
    res = client.get("/api/media")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["items"]) >= 1
    assert data["items"][0]["title"] == "Sample Test Book"

    # Detail
    res = client.get(f"/api/media/{book_id}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["title"] == "Sample Test Book"

    # Edit metadata and assign series (admin only)
    _login_admin(client, app)
    res = client.post(
        f"/api/media/{book_id}/edit",
        json={
            "title": "Updated Sample Title",
            "series": "Sample Chronicles",
            "series_index": "1.5",
            "authors": "Author Alpha, Author Beta",
            "tags": "Sci-Fi, Space",
        },
    )
    assert res.status_code == 200
    edit_data = res.get_json()
    assert edit_data["status"] == "success"
    assert edit_data["item"]["series"] == "Sample Chronicles"
    assert edit_data["item"]["series_index"] == 1.5
    assert len(edit_data["item"]["authors"]) == 2

    # Verify series in UI / API
    res = client.get(f"/api/media/{book_id}")
    assert res.status_code == 200
    assert res.get_json()["series"] == "Sample Chronicles"
    assert res.get_json()["media_type"] == "book"

    # Verify media_type filtering in list_media
    res = client.get("/api/media?media_type=book")
    assert res.status_code == 200
    assert len(res.get_json()["items"]) >= 1
    res = client.get("/api/media?media_type=video")
    assert res.status_code == 200
    assert len(res.get_json()["items"]) == 0

    # Save Progress
    res = client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "epubcfi(/6/2[chapter1]!/4/2/10)", "percentage": 42.5},
    )
    assert res.status_code == 200
    assert res.get_json()["percentage"] == 42.5

    # Get Progress
    res = client.get(f"/api/media/{book_id}/progress")
    assert res.status_code == 200
    assert res.get_json()["percentage"] == 42.5


def test_api_libraries_and_library_filter(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None

    # Test GET /api/libraries
    res = client.get("/api/libraries")
    assert res.status_code == 200
    data = res.get_json()
    assert "libraries" in data
    assert len(data["libraries"]) >= 1
    lib = data["libraries"][0]
    assert "id" in lib
    assert "name" in lib
    assert "path" in lib
    assert "count" in lib

    # Test GET /api/media?library=
    res = client.get(f"/api/media?library={lib['id']}")
    assert res.status_code == 200
    books_data = res.get_json()
    assert "items" in books_data


def test_api_library_crud_and_media_type_selection(client, app, tmp_path, sample_epub):
    import shutil

    # Admin operations require an authenticated admin user
    _login_admin(client, app)

    # Create a new custom media directory with an epub file
    custom_media_dir = tmp_path / "my_comics_folder"
    custom_media_dir.mkdir()
    shutil.copy(sample_epub, custom_media_dir / "manga1.epub")

    # 1. POST /api/libraries - Add new media folder with media_type="comic"
    add_res = client.post(
        "/api/libraries",
        json={
            "path": str(custom_media_dir),
            "name": "My Comics",
            "media_type": "comic",
        },
    )
    assert add_res.status_code == 201
    add_data = add_res.get_json()
    assert add_data["status"] == "success"
    assert add_data["library"]["name"] == "My Comics"
    assert add_data["library"]["media_type"] == "comic"
    lib_id = add_data["library"]["id"]
    db_id = add_data["library"]["db_id"]

    # Verify that the book scanned in this folder inherited media_type="comic"
    books_res = client.get(f"/api/media?library={lib_id}")
    assert books_res.status_code == 200
    items = books_res.get_json()["items"]
    assert len(items) == 1
    assert items[0]["media_type"] == "comic"

    # 2. GET /api/libraries/<identifier>
    info_res = client.get(f"/api/libraries/{lib_id}")
    assert info_res.status_code == 200
    assert info_res.get_json()["library"]["media_type"] == "comic"
    assert info_res.get_json()["library"]["count"] == 1

    # By numeric db_id as well
    info_db_res = client.get(f"/api/libraries/{db_id}")
    assert info_db_res.status_code == 200
    assert info_db_res.get_json()["library"]["id"] == lib_id

    # 3. PUT /api/libraries/<identifier> - Change media_type to "video"
    put_res = client.put(
        f"/api/libraries/{lib_id}",
        json={"media_type": "video", "name": "My Videos"},
    )
    assert put_res.status_code == 200
    assert put_res.get_json()["library"]["media_type"] == "video"
    assert put_res.get_json()["library"]["name"] == "My Videos"

    # Verify that book in that folder was updated to "video"
    book_check = client.get(f"/api/media/{items[0]['id']}")
    assert book_check.status_code == 200
    assert book_check.get_json()["media_type"] == "video"

    # 4. POST /api/libraries/<identifier>/scan - Single library scan (sync mode)
    scan_res = client.post(f"/api/libraries/{lib_id}/scan?sync=true")
    assert scan_res.status_code == 200
    assert scan_res.get_json()["status"] == "success"

    # 5. DELETE /api/libraries/<identifier> - Remove library
    del_res = client.delete(f"/api/libraries/{lib_id}")
    assert del_res.status_code == 200
    assert del_res.get_json()["status"] == "success"

    # Verify deleted from /api/libraries
    list_res = client.get("/api/libraries")
    assert list_res.status_code == 200
    ids = [item["id"] for item in list_res.get_json()["libraries"]]
    assert lib_id not in ids


def test_api_media_and_legacy_routes(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        book_id = book.id

    # Test standardized /api/media endpoint
    res_media = client.get("/api/media")
    assert res_media.status_code == 200
    assert len(res_media.get_json()["items"]) >= 1

    # Obsolete /api/books and /api/items routes return 404
    assert client.get("/api/books").status_code == 404
    assert client.get("/api/items").status_code == 404

    # Detail endpoints
    res_med = client.get(f"/api/media/{book_id}")
    assert res_med.status_code == 200
    assert res_med.get_json()["id"] == book_id
    assert client.get(f"/api/books/{book_id}").status_code == 404
    assert client.get(f"/api/items/{book_id}").status_code == 404

    # File & cover endpoints
    res_file = client.get(f"/api/media/{book_id}/file")
    assert res_file.status_code == 200
    res_cover = client.get(f"/api/media/{book_id}/cover")
    assert res_cover.status_code in (200, 302)

    # Progress endpoint
    res_prog = client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "0.5", "percentage": 50.0},
    )
    assert res_prog.status_code == 200
    assert res_prog.get_json()["percentage"] == 50.0

    # Stream endpoint
    res_stream_media = client.get(f"/api/media/{book_id}/stream")
    assert res_stream_media.status_code == 200
    assert res_stream_media.headers["Content-Type"].startswith("application/epub+zip")
    assert client.get(f"/api/books/{book_id}/stream").status_code == 404

    # UI routes: /media/<id> gives 200, /book/<id> and /item/<id> 301 redirect to /media/<id>
    ui_media = client.get(f"/media/{book_id}")
    assert ui_media.status_code == 200

    ui_book = client.get(f"/book/{book_id}", follow_redirects=False)
    assert ui_book.status_code == 301
    assert ui_book.headers["Location"].endswith(f"/media/{book_id}")

    ui_item = client.get(f"/item/{book_id}", follow_redirects=False)
    assert ui_item.status_code == 301
    assert ui_item.headers["Location"].endswith(f"/media/{book_id}")


def test_api_bookmark_authorization(client, app, sample_epub):
    from aarkib.extensions import db
    from aarkib.models import Bookmark

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        u1 = User(username="reader_bm", is_admin=False)
        u1.set_password("pass")
        db.session.add(u1)
        db.session.commit()

        bm = Bookmark(
            user_id=u1.id,
            book_id=book.id,
            location="epubcfi(/6/4)",
            title="User Bookmark",
        )
        db.session.add(bm)
        db.session.commit()
        bm_id = bm.id

    # Unauthenticated attempt to delete user1's bookmark should return 403 without crashing
    res_del_unauth = client.delete(f"/api/bookmarks/{bm_id}")
    assert res_del_unauth.status_code == 403
