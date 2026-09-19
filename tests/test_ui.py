def test_ui_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Aarkib" in response.data
    assert b"Library" in response.data
    assert b'class="nav-search"' in response.data
    assert b'id="search-input"' in response.data
    assert b'class="subnav-bar"' not in response.data
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

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    response = client.get(f"/media/{book_id}")
    assert response.status_code == 200
    assert b"Sample Test Book" in response.data
    assert b"Read in Browser (EPUB)" in response.data
    assert b"Fetch Metadata Online" in response.data


def test_reader_epub_page(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
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

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
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

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_cbz, covers_dir)
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

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_cbz, covers_dir)
        book_id = book.id

    res = client.get(f"/reader/cbz/{book_id}")
    assert res.status_code == 200
    assert b"Continuous (Webtoon)" in res.data
    assert b"webtoon-width-select" in res.data
    assert b"reader-cbz.js" in res.data
    assert b"reader-cbz.css" in res.data


def test_epub_download_split_button_and_dropdown(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
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

    from aarkib.services.indexer import index_media_file

    # Empty state initially
    res_empty = client.get("/")
    assert res_empty.status_code == 200
    assert b"No media found in library" in res_empty.data

    # Index a book
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        index_media_file(sample_epub, covers_dir)

    res = client.get("/")
    assert res.status_code == 200
    assert b"Recently Added" in res.data
    assert b"Sample Test Book" in res.data
    assert b"dashboard-shelves" in res.data
    assert b"view-shelves-btn" in res.data


def test_homepage_in_progress_row(client, app, sample_epub):
    from pathlib import Path

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
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
    from aarkib.services.indexer import index_media_file

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
        index_media_file(manga_file, covers_dir)
        index_media_file(novel_file, covers_dir)

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


def test_authors_view_direct(client, app, sample_epub):
    """Directly verifies GET /authors renders author list."""
    from pathlib import Path

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        index_media_file(sample_epub, covers_dir)

    res = client.get("/authors")
    assert res.status_code == 200
    assert b"Authors" in res.data or b"Creators" in res.data
    assert b"Jane Doe" in res.data
    assert b'class="subnav-bar"' in res.data


def test_series_view_direct(client, app, sample_epub):
    """Directly verifies GET /series renders series list."""
    from pathlib import Path

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        index_media_file(sample_epub, covers_dir)

    res = client.get("/series")
    assert res.status_code == 200
    assert b"Series" in res.data or b"Collections" in res.data


def test_tags_view_direct(client, app, sample_epub):
    """Directly verifies GET /tags renders tag list."""
    from pathlib import Path

    from aarkib.services.indexer import index_media_file

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        index_media_file(sample_epub, covers_dir)

    res = client.get("/tags")
    assert res.status_code == 200
    assert b"Tags" in res.data


def test_settings_system_health_view(client):
    """Verify /settings/system renders the System Health & Diagnostics card."""
    res = client.get("/settings/system")
    assert res.status_code == 200
    assert (
        b"System Health &amp; Diagnostics" in res.data
        or b"System Health & Diagnostics" in res.data
    )
    assert b"SQLite" in res.data
    assert b"FTS5 Search Engine" in res.data


def test_settings_backup_view(client):
    """Verify /settings/backup renders the Backup & Disaster Recovery panel."""
    res = client.get("/settings/backup")
    assert res.status_code == 200
    assert (
        b"Backup &amp; Disaster Recovery" in res.data
        or b"Backup & Disaster Recovery" in res.data
    )
    assert b"Create Backup" in res.data
    assert b"Upload &amp; Restore" in res.data or b"Upload & Restore" in res.data


def test_settings_plugins_api_key_ui(client, app):
    """Verify that /settings/plugins renders API Key configuration fields for video, books, and podcast plugins."""
    from aarkib.services.settings_service import update_settings

    # Initially keys not set
    res = client.get("/settings/plugins")
    assert res.status_code == 200
    assert b"API Key Settings" in res.data
    assert b"TMDB API Key" in res.data
    assert b"ComicVine API Key" in res.data
    assert b"plugin-input-TMDB_API_KEY" in res.data
    assert b"Key Not Set" in res.data

    # Now set TMDB API key
    update_settings(app, {"TMDB_API_KEY": "test_tmdb_key_12345"})
    res_set = client.get("/settings/plugins")
    assert res_set.status_code == 200
    assert b"Configured" in res_set.data
    assert b"Active" in res_set.data


def test_settings_system_api_key_ui(client, app):
    """Verify that /settings/system renders API key fields in the Metadata Enrichment card."""
    res = client.get("/settings/system")
    assert res.status_code == 200
    assert b"TMDB API Key (Video Plugin)" in res.data
    assert b"ComicVine API Key (Books &amp; Comics Plugin)" in res.data
    assert b"setting-TMDB_API_KEY" in res.data
    assert b"setting-COMICVINE_API_KEY" in res.data
    assert b"setting-PODCASTINDEX_API_KEY" in res.data
    assert b"setting-GOOGLE_BOOKS_API_KEY" in res.data


def test_reader_video_diagnostics_overlay(client, app, tmp_path):
    """Verify that video reader renders playback diagnostics button and overlay HUD."""

    from aarkib.extensions import db
    from aarkib.models.media_item import MediaItem

    with app.app_context():
        video_file = tmp_path / "diag_test.mp4"
        video_file.write_bytes(b"dummy_mp4_bytes")

        video_item = MediaItem(
            title="Diagnostics Test Video",
            original_file_path=str(video_file),
            file_format="mp4",
            media_type="video",
            file_size=1024,
            file_hash="hash_diag_1",
            codec="h264",
        )
        db.session.add(video_item)
        db.session.commit()
        video_id = video_item.id

    res = client.get(f"/reader/video/{video_id}")
    assert res.status_code == 200
    assert b'id="stats-btn"' in res.data
    assert b'id="diagnostics-overlay"' in res.data
    assert b"Playback Diagnostics" in res.data
    assert b"toggleDiagnosticsHUD()" in res.data
    assert b'id="diag-client"' in res.data
    assert b'id="diag-mode"' in res.data
    assert b'id="diag-hwaccel"' in res.data
    assert b'id="diag-reasons"' in res.data


def test_settings_system_live_jobs_manager(client):
    """Verify that /settings/system renders the live background job manager."""
    res = client.get("/settings/system")
    assert res.status_code == 200
    assert b'id="live-jobs-container"' in res.data
    assert b"Background Jobs Live Manager" in res.data
    assert b"refreshLiveJobs()" in res.data
    assert b"cancelLiveJob" in res.data
    assert b"retryLiveJob" in res.data


def test_settings_users_profiles_and_acl_matrix(client):
    """Verify that /settings/users renders family profile management and library ACL matrix."""
    res = client.get("/settings/users")
    assert res.status_code == 200
    assert b'id="profiles-management"' in res.data
    assert (
        b"Family Profiles &amp; Library Access Control" in res.data
        or b"Family Profiles & Library Access Control" in res.data
    )
    assert b'id="add-profile-box"' in res.data
    assert b'id="acl-modal"' in res.data
    assert b"acl-table" in res.data
    assert b"saveAclPermissions()" in res.data
    assert b"openAclModal" in res.data
    assert b"btn-profile-acl" in res.data
