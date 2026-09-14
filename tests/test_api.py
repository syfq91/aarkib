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
    assert edit_data["item"]["provenance"]["title"] == "user"

    # Verify series in UI / API
    res = client.get(f"/api/media/{book_id}")
    assert res.status_code == 200
    assert res.get_json()["series"] == "Sample Chronicles"
    assert res.get_json()["media_type"] == "book"
    assert res.get_json()["provenance"]["title"] == "user"
    assert "locked_fields" in res.get_json()

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

    res_med = client.get(f"/api/media/{book_id}")
    assert res_med.status_code == 200
    assert res_med.get_json()["id"] == book_id
    assert "provenance" in res_med.get_json()
    assert "locked_fields" in res_med.get_json()
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

    # UI route: /media/<id> gives 200; retired legacy routes return 404
    ui_media = client.get(f"/media/{book_id}")
    assert ui_media.status_code == 200
    assert client.get(f"/book/{book_id}").status_code == 404
    assert client.get(f"/item/{book_id}").status_code == 404


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

    # Unauthenticated attempt to delete user1's bookmark should return 401 or 403 without crashing
    res_del_unauth = client.delete(f"/api/bookmarks/{bm_id}")
    assert res_del_unauth.status_code in (401, 403)


def test_api_browse_directories(client, app, tmp_path):
    from aarkib.extensions import db
    from aarkib.models import User

    # Unauthenticated request returns 401 or 403
    client.get("/auth/logout")
    res_unauth = client.get("/api/fs/directories")
    assert res_unauth.status_code in (302, 401, 403)

    # Login as normal reader
    reader = User(username="reader_fs", is_admin=False)
    reader.set_password("readerpass")
    with app.app_context():
        db.session.add(reader)
        db.session.commit()
    client.post("/auth/login", data={"username": "reader_fs", "password": "readerpass"})
    res_reader = client.get("/api/fs/directories")
    assert res_reader.status_code == 403

    # Login as admin
    client.get("/auth/logout")
    _login_admin(client, app)

    # Create dummy folder structure in tmp_path
    parent_dir = tmp_path / "browse_root"
    parent_dir.mkdir()
    (parent_dir / "folder_alpha").mkdir()
    (parent_dir / "folder_beta").mkdir()
    (parent_dir / ".hidden_folder").mkdir()
    (parent_dir / "file.txt").write_text("not a folder")

    # Browse parent_dir
    res = client.get(f"/api/fs/directories?path={parent_dir}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["current_path"] == str(parent_dir.resolve())
    assert "parent_path" in data
    assert "quick_locations" in data

    dir_names = [d["name"] for d in data["directories"]]
    assert "folder_alpha" in dir_names
    assert "folder_beta" in dir_names
    # Hidden folders and files should be excluded
    assert ".hidden_folder" not in dir_names
    assert "file.txt" not in dir_names


def test_library_media_types_movie_tv_podcast_and_books(
    client, app, tmp_path, sample_epub, sample_cbz
):
    import shutil

    _login_admin(client, app)

    # 1. Movie library
    movie_dir = tmp_path / "movies"
    movie_dir.mkdir()
    fake_movie = movie_dir / "Inception (2010).mp4"
    fake_movie.write_bytes(b"dummy movie data")

    add_res = client.post(
        "/api/libraries",
        json={
            "path": str(movie_dir),
            "name": "Feature Films",
            "media_type": "movie",
        },
    )
    assert add_res.status_code == 201
    movie_lib_id = add_res.get_json()["library"]["id"]

    # Verify item has media_type="movie"
    media_res = client.get(f"/api/media?library={movie_lib_id}")
    assert media_res.status_code == 200
    items = media_res.get_json()["items"]
    assert len(items) == 1
    assert items[0]["media_type"] == "movie"

    # Query with media_type=movie
    res_m = client.get("/api/media?media_type=movie")
    assert res_m.status_code == 200
    assert any(i["id"] == items[0]["id"] for i in res_m.get_json()["items"])

    # 2. TV library
    tv_dir = tmp_path / "tv_shows"
    tv_dir.mkdir()
    fake_tv = tv_dir / "Breaking Bad S01E01.mp4"
    fake_tv.write_bytes(b"dummy tv data")

    add_tv_res = client.post(
        "/api/libraries",
        json={
            "path": str(tv_dir),
            "name": "TV Shows",
            "media_type": "tv",
        },
    )
    assert add_tv_res.status_code == 201
    tv_lib_id = add_tv_res.get_json()["library"]["id"]

    media_tv_res = client.get(f"/api/media?library={tv_lib_id}")
    assert media_tv_res.status_code == 200
    tv_items = media_tv_res.get_json()["items"]
    assert len(tv_items) == 1
    assert tv_items[0]["media_type"] == "tv"

    # Query with media_type=tv
    res_tv = client.get("/api/media?media_type=tv")
    assert res_tv.status_code == 200
    assert any(i["id"] == tv_items[0]["id"] for i in res_tv.get_json()["items"])

    # 3. Podcast library
    pod_dir = tmp_path / "podcasts"
    pod_dir.mkdir()
    fake_pod = pod_dir / "Episode 1.mp3"
    fake_pod.write_bytes(b"dummy pod data")

    add_pod_res = client.post(
        "/api/libraries",
        json={
            "path": str(pod_dir),
            "name": "Tech Podcasts",
            "media_type": "podcast",
        },
    )
    assert add_pod_res.status_code == 201
    pod_lib_id = add_pod_res.get_json()["library"]["id"]

    media_pod_res = client.get(f"/api/media?library={pod_lib_id}")
    assert media_pod_res.status_code == 200
    pod_items = media_pod_res.get_json()["items"]
    assert len(pod_items) == 1
    assert pod_items[0]["media_type"] == "podcast"

    # 4. Books library with both EPUB and CBZ
    books_dir = tmp_path / "unified_books"
    books_dir.mkdir()
    shutil.copy(sample_epub, books_dir / "novel.epub")
    shutil.copy(sample_cbz, books_dir / "comic.cbz")

    add_books_res = client.post(
        "/api/libraries",
        json={
            "path": str(books_dir),
            "name": "My Bookshelf",
            "media_type": "book",
        },
    )
    assert add_books_res.status_code == 201
    book_lib_id = add_books_res.get_json()["library"]["id"]

    media_book_res = client.get(f"/api/media?library={book_lib_id}")
    assert media_book_res.status_code == 200
    book_items = media_book_res.get_json()["items"]
    assert len(book_items) == 2

    # Query with media_type=book should return both
    res_book = client.get("/api/media?media_type=book")
    assert res_book.status_code == 200
    retrieved_ids = {i["id"] for i in res_book.get_json()["items"]}
    for bi in book_items:
        assert bi["id"] in retrieved_ids

    # Verify CBZ item opens CBZ reader
    cbz_item = next(i for i in book_items if i["file_format"] == "cbz")
    with app.app_context():
        from aarkib.extensions import db
        from aarkib.models import MediaItem

        cbz_model = db.session.get(MediaItem, cbz_item["id"])
        assert cbz_model.is_comic is True
        assert cbz_model.is_book is False
        assert "/reader/cbz/" in cbz_model.player_url
