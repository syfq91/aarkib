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

    # List books
    res = client.get("/api/books")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["books"]) >= 1
    assert data["books"][0]["title"] == "Sample Test Book"

    # Detail
    res = client.get(f"/api/books/{book_id}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["title"] == "Sample Test Book"

    # Edit metadata and assign series (admin only)
    _login_admin(client, app)
    res = client.post(
        f"/api/books/{book_id}/edit",
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
    assert edit_data["book"]["series"] == "Sample Chronicles"
    assert edit_data["book"]["series_index"] == 1.5
    assert len(edit_data["book"]["authors"]) == 2

    # Verify series in UI / API
    res = client.get(f"/api/books/{book_id}")
    assert res.status_code == 200
    assert res.get_json()["series"] == "Sample Chronicles"
    assert res.get_json()["media_type"] == "book"

    # Verify media_type filtering in list_books
    res = client.get("/api/books?media_type=book")
    assert res.status_code == 200
    assert len(res.get_json()["books"]) >= 1
    res = client.get("/api/books?media_type=video")
    assert res.status_code == 200
    assert len(res.get_json()["books"]) == 0

    # Save Progress
    res = client.post(
        f"/api/books/{book_id}/progress",
        json={"location": "epubcfi(/6/2[chapter1]!/4/2/10)", "percentage": 42.5},
    )
    assert res.status_code == 200
    assert res.get_json()["percentage"] == 42.5

    # Get Progress
    res = client.get(f"/api/books/{book_id}/progress")
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

    # Test GET /api/books?library=
    res = client.get(f"/api/books?library={lib['id']}")
    assert res.status_code == 200
    books_data = res.get_json()
    assert "books" in books_data


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
    books_res = client.get(f"/api/books?library={lib_id}")
    assert books_res.status_code == 200
    books = books_res.get_json()["books"]
    assert len(books) == 1
    assert books[0]["media_type"] == "comic"

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
    book_check = client.get(f"/api/books/{books[0]['id']}")
    assert book_check.status_code == 200
    assert book_check.get_json()["media_type"] == "video"

    # 4. POST /api/libraries/<identifier>/scan - Single library scan
    scan_res = client.post(f"/api/libraries/{lib_id}/scan")
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
