def test_ui_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Aarkib" in response.data
    assert b"Library" in response.data
    assert b'class="nav-search"' in response.data
    assert b'id="search-input"' in response.data
    assert b'class="subnav-bar"' in response.data
    assert b'href="/authors"' in response.data
    assert b'href="/series"' in response.data
    assert b'href="/tags"' in response.data
    assert b'id="scan-now-btn"' not in response.data


def test_pwa_manifest(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert "application/manifest+json" in response.headers["Content-Type"]
    assert b"Aarkib Media Server" in response.data


def test_service_worker(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "application/javascript" in response.headers["Content-Type"]


def test_book_detail_page(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    response = client.get(f"/media/{book_id}")
    assert response.status_code == 200
    assert b"Sample Test Book" in response.data
    assert b"Read in Browser (EPUB)" in response.data
    assert b"Fetch Metadata Online" in response.data


def test_reader_epub_page(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    response = client.get(f"/reader/epub/{book_id}")
    assert response.status_code == 200
    assert b"reader-epub.js" in response.data
    assert b"epub.min.js" in response.data
    assert b"jszip.min.js" in response.data
    assert b"/file" in response.data

    # Also test file route
    file_res = client.get(f"/api/media/{book_id}/file")
    assert file_res.status_code == 200
    assert file_res.headers["Content-Type"] == "application/epub+zip"


def test_reader_epub_resume_progress(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        book_id = book.id

    # Save progress via API
    cfi = "epubcfi(/6/2[chapter1]!/4/2/14)"
    res = client.post(
        f"/api/media/{book_id}/progress", json={"location": cfi, "percentage": 35.0}
    )
    assert res.status_code == 200

    # Reopen reader
    reader_res = client.get(f"/reader/epub/{book_id}")
    assert reader_res.status_code == 200
    assert cfi.encode("utf-8") in reader_res.data

    # Book detail page should show Continue Reading
    detail_res = client.get(f"/media/{book_id}")
    assert detail_res.status_code == 200
    assert b"Continue Reading (35%)" in detail_res.data


def test_reader_cbz_resume_progress(client, app, sample_cbz):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_cbz, covers_dir)
        book_id = book.id

    # Initial CBZ reader
    reader_res = client.get(f"/reader/cbz/{book_id}")
    assert reader_res.status_code == 200
    assert b'data-initial-page="1"' in reader_res.data

    # Save progress via API (e.g. Page 2)
    res = client.post(
        f"/api/media/{book_id}/progress", json={"location": "2", "percentage": 50.0}
    )
    assert res.status_code == 200

    # Reopen CBZ reader -> initial_page should be 2
    reader_res = client.get(f"/reader/cbz/{book_id}")
    assert reader_res.status_code == 200
    assert b'data-initial-page="2"' in reader_res.data
    assert b"INITIAL_PAGE = 2" in reader_res.data

    # Book detail page should show Continue Reading
    detail_res = client.get(f"/media/{book_id}")
    assert detail_res.status_code == 200
    assert b"Continue Reading (50%)" in detail_res.data


def test_reader_cbz_webtoon_support(client, app, sample_cbz):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_cbz, covers_dir)
        book_id = book.id

    res = client.get(f"/reader/cbz/{book_id}")
    assert res.status_code == 200
    assert b"Continuous (Webtoon)" in res.data
    assert b"webtoon-width-select" in res.data
    assert b"reader-cbz.js" in res.data
    assert b"reader-cbz.css" in res.data


def test_epub_download_split_button_and_dropdown(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        book_id = book.id

    res = client.get(f"/media/{book_id}")
    assert res.status_code == 200
    # Original download link
    assert f'href="/api/media/{book_id}/download"'.encode() in res.data
    # E-Ink dropdown trigger
    assert b'id="eink-dropdown-btn"' in res.data
    # Optimized preset links
    assert f'href="/api/media/{book_id}/download/optimized/x4"'.encode() in res.data
    assert f'href="/api/media/{book_id}/download/optimized/x3"'.encode() in res.data
    assert f'href="/api/media/{book_id}/download/optimized/kindle"'.encode() in res.data
    assert f'href="/api/media/{book_id}/download/optimized/kobo"'.encode() in res.data
    assert f'href="/api/media/{book_id}/download/optimized/eink"'.encode() in res.data


def test_settings_page_displays_multiple_directories(tmp_path):
    from aarkib import create_app
    from aarkib.config import TestConfig

    dir1 = tmp_path / "lib1"
    dir2 = tmp_path / "lib2"
    dir1.mkdir()
    dir2.mkdir()

    class MultiDirTestConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [dir1, dir2]
        MEDIA_DIR = dir1
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/settings_test.db"

    app = create_app(MultiDirTestConfig)
    client = app.test_client()

    with app.app_context():
        from aarkib.extensions import db
        from aarkib.models import User

        db.create_all()
        user = User(username="reader", is_admin=False)
        user.set_password("pass")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    res = client.get("/settings")
    assert res.status_code == 200
    assert b"Library Storage (2 folders):" in res.data
    assert str(dir1).encode() in res.data
    assert str(dir2).encode() in res.data

    res_lib = client.get("/settings/libraries")
    assert res_lib.status_code == 200
    assert b"Library Storage (2 folders):" in res_lib.data
    assert str(dir1).encode() in res_lib.data
    assert str(dir2).encode() in res_lib.data


def test_homepage_multi_row_recently_added_and_empty_state(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    # Empty state initially
    res_empty = client.get("/")
    assert res_empty.status_code == 200
    assert b"No media found in library" in res_empty.data

    # Index a book
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        index_single_book(sample_epub, covers_dir)

    res = client.get("/")
    assert res.status_code == 200
    assert b"Recently Added" in res.data
    assert b"Sample Test Book" in res.data
    assert b"dashboard-shelves" in res.data
    assert b"view-shelves-btn" in res.data


def test_homepage_in_progress_row(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        book_id = book.id

    # Initially before reading, no Continue shelf
    res_before = client.get("/")
    assert res_before.status_code == 200
    assert b"Continue Watching & Reading" not in res_before.data

    # Save 45% progress
    res_prog = client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "cfi_step_1", "percentage": 45.0, "is_completed": False},
    )
    assert res_prog.status_code == 200

    # Homepage should now render Continue shelf
    res_after = client.get("/")
    assert res_after.status_code == 200
    assert b"Continue Watching & Reading" in res_after.data
    assert b"45%" in res_after.data
    assert b"Resume" in res_after.data

    # Complete book (100%)
    client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "end", "percentage": 100.0, "is_completed": True},
    )
    res_completed = client.get("/")
    assert res_completed.status_code == 200
    assert b"Continue Watching & Reading" not in res_completed.data


def test_homepage_dynamic_library_shelves_multiple(tmp_path, sample_epub, sample_cbz):
    import shutil
    from pathlib import Path

    from aarkib import create_app
    from aarkib.config import TestConfig
    from aarkib.extensions import db
    from aarkib.services.scanner import index_single_book

    manga_dir = tmp_path / "manga"
    novels_dir = tmp_path / "novels"
    manga_dir.mkdir()
    novels_dir.mkdir()

    # Place files in respective directories
    manga_file = manga_dir / "manga1.cbz"
    shutil.copyfile(sample_cbz, manga_file)

    novel_file = novels_dir / "novel1.epub"
    shutil.copyfile(sample_epub, novel_file)

    class MultiLibConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [novels_dir, manga_dir]
        MEDIA_DIR = novels_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/multi_lib.db"

    app = create_app(MultiLibConfig)
    client = app.test_client()

    with app.app_context():
        from aarkib.models import User

        db.create_all()
        user = User(username="admin", is_admin=True)
        user.set_password("pass")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        covers_dir = Path(app.config["COVERS_DIR"])
        index_single_book(manga_file, covers_dir)
        index_single_book(novel_file, covers_dir)

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    res = client.get("/")
    assert res.status_code == 200
    # Both library shelves should be dynamically generated and rendered
    assert b"Novels" in res.data
    assert b"Manga" in res.data
    assert b"Recently Added" in res.data
    assert b"shelf-lib-novels" in res.data
    assert b"shelf-lib-manga" in res.data
