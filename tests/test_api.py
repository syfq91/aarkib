from pathlib import Path

from buukuu.services.parsers.epub import extract_series_from_title
from buukuu.services.scanner import index_single_book


def test_extract_series_from_title():
    s, idx, title = extract_series_from_title("[One Piece 01] Romance Dawn")
    assert s == "One Piece"
    assert idx == 1.0
    assert title == "Romance Dawn"

    s, idx, title = extract_series_from_title(
        "Harry Potter - Book 2 - Chamber of Secrets"
    )
    assert s == "Harry Potter"
    assert idx == 2.0
    assert title == "Chamber of Secrets"

    s, idx, title = extract_series_from_title("Dune #1 - Dune")
    assert s == "Dune"
    assert idx == 1.0
    assert title == "Dune"


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

    # Edit metadata and assign series
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
