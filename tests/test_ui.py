def test_ui_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Buukuu" in response.data
    assert b"Library" in response.data


def test_pwa_manifest(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert "application/manifest+json" in response.headers["Content-Type"]
    assert b"Buukuu Book Server" in response.data


def test_service_worker(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "application/javascript" in response.headers["Content-Type"]


def test_book_detail_page(client, app, sample_epub):
    from pathlib import Path

    from buukuu.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    response = client.get(f"/book/{book_id}")
    assert response.status_code == 200
    assert b"Sample Test Book" in response.data
    assert b"Read in Browser (EPUB)" in response.data
    assert b"Fetch Metadata Online" in response.data


def test_reader_epub_page(client, app, sample_epub):
    from pathlib import Path

    from buukuu.services.scanner import index_single_book

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
    assert b"book.epub" in response.data

    # Also test file route
    file_res = client.get(f"/api/books/{book_id}/book.epub")
    assert file_res.status_code == 200
    assert file_res.headers["Content-Type"] == "application/epub+zip"


def test_reader_epub_resume_progress(client, app, sample_epub):
    from pathlib import Path

    from buukuu.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        book_id = book.id

    # Save progress via API
    cfi = "epubcfi(/6/2[chapter1]!/4/2/14)"
    res = client.post(f"/api/books/{book_id}/progress", json={"location": cfi, "percentage": 35.0})
    assert res.status_code == 200

    # Reopen reader
    reader_res = client.get(f"/reader/epub/{book_id}")
    assert reader_res.status_code == 200
    assert cfi.encode("utf-8") in reader_res.data

    # Book detail page should show Continue Reading
    detail_res = client.get(f"/book/{book_id}")
    assert detail_res.status_code == 200
    assert b"Continue Reading (35%)" in detail_res.data


def test_reader_cbz_resume_progress(client, app, sample_cbz):
    from pathlib import Path

    from buukuu.services.scanner import index_single_book

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_cbz, covers_dir)
        book_id = book.id

    # Initial CBZ reader
    reader_res = client.get(f"/reader/cbz/{book_id}")
    assert reader_res.status_code == 200
    assert b'data-initial-page="1"' in reader_res.data

    # Save progress via API (e.g. Page 2)
    res = client.post(f"/api/books/{book_id}/progress", json={"location": "2", "percentage": 50.0})
    assert res.status_code == 200

    # Reopen CBZ reader -> initial_page should be 2
    reader_res = client.get(f"/reader/cbz/{book_id}")
    assert reader_res.status_code == 200
    assert b'data-initial-page="2"' in reader_res.data
    assert b'INITIAL_PAGE = 2' in reader_res.data

    # Book detail page should show Continue Reading
    detail_res = client.get(f"/book/{book_id}")
    assert detail_res.status_code == 200
    assert b"Continue Reading (50%)" in detail_res.data
