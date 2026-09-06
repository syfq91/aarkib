from aarkib.extensions import db
from aarkib.models import Author, Book, User


def test_opds_root_catalog(client):
    response = client.get("/opds")
    assert response.status_code == 200
    assert "application/atom+xml" in response.headers["Content-Type"]
    assert b"Aarkib Catalog" in response.data


def test_opds_recent_feed(client, app):
    with app.app_context():
        book = Book(
            title="OPDS Test Book",
            original_file_path="/path/test.epub",
            file_format="epub",
            file_hash="hash987",
            description="Testing OPDS Feed Generation",
        )
        author = Author(name="OPDS Author")
        book.authors.append(author)
        db.session.add_all([book, author])
        db.session.commit()

    response = client.get("/opds/recent")
    assert response.status_code == 200
    assert "application/atom+xml" in response.headers["Content-Type"]
    assert b"OPDS Test Book" in response.data
    assert b"OPDS Author" in response.data
    assert b"http://opds-spec.org/acquisition" in response.data
    assert b"http://opds-spec.org/progression" in response.data
    assert b"application/opds-progression+json" in response.data


def test_opds_search(client):
    response = client.get("/opds/search?q=Test")
    assert response.status_code == 200
    assert "application/atom+xml" in response.headers["Content-Type"]


def test_opds_authentication_document(client):
    response = client.get("/opds/authentication.json")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/opds-authentication+json"
    data = response.get_json()
    assert "authentication" in data
    assert data["authentication"][0]["type"] == "http://opds-spec.org/auth/basic"


def test_opds_progression_crud_and_conflicts(client, app):
    with app.app_context():
        # Create user & book
        user = User(username="progression_user")
        user.set_password("pass123")
        book = Book(
            title="Progression Book",
            original_file_path="/path/prog.epub",
            file_format="epub",
            file_hash="hashprog123",
        )
        db.session.add_all([user, book])
        db.session.commit()
        book_id = book.id

    # 1. Fetch initial progression (empty)
    res = client.get(f"/opds/books/{book_id}/progression")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/opds-progression+json"
    assert res.get_json() == {}

    # 2. Update progression via PUT
    put_payload = {
        "title": "Chapter 1 - A New Dawn",
        "modified": "2026-02-01T12:00:00Z",
        "device": {
            "id": "urn:uuid:019c0047-cc8d-7ec4-a3c3-938ccadc020a",
            "name": "Ebook Reader (Pixel 10 Pro)",
        },
        "progression": 0.425,
        "references": ["chapter1.html#:~:text=It%20was%20expected"],
    }
    res = client.put(f"/opds/books/{book_id}/progression", json=put_payload)
    assert res.status_code == 201
    assert res.headers["Content-Type"] == "application/opds-progression+json"
    data = res.get_json()
    assert data["title"] == "Chapter 1 - A New Dawn"
    assert data["progression"] == 0.425
    assert data["device"]["name"] == "Ebook Reader (Pixel 10 Pro)"
    assert data["references"] == ["chapter1.html#:~:text=It%20was%20expected"]

    # 3. GET should now return the updated document
    res = client.get(f"/opds/books/{book_id}/progression")
    assert res.status_code == 200
    data = res.get_json()
    assert data["progression"] == 0.425
    assert data["title"] == "Chapter 1 - A New Dawn"

    # 4. Conflict check: sending an older timestamp returns 409 Conflict
    older_payload = {
        "title": "Old chapter",
        "modified": "2025-01-01T00:00:00Z",
        "device": {
            "id": "urn:uuid:019c0047-cc8d-7ec4-a3c3-938ccadc020a",
            "name": "Ebook Reader (Pixel 10 Pro)",
        },
        "progression": 0.1,
    }
    res = client.put(f"/opds/books/{book_id}/progression", json=older_payload)
    assert res.status_code == 409
    assert res.headers["Content-Type"] == "application/problem+json"
    assert "progression-date" in res.get_json()["type"]

    # 5. Invalid payload check
    res = client.put(f"/opds/books/{book_id}/progression", json={"foo": "bar"})
    assert res.status_code == 400
    assert res.headers["Content-Type"] == "application/problem+json"


def test_opds2_catalog_and_recent(client, app):
    with app.app_context():
        book = Book(
            title="OPDS 2.0 Test Book",
            original_file_path="/path/test2.epub",
            file_format="epub",
            file_hash="hashopds2",
            description="Testing OPDS 2.0 JSON Catalog",
        )
        db.session.add(book)
        db.session.commit()

    res = client.get("/opds/v2/catalog.json")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/opds+json"
    data = res.get_json()
    assert "navigation" in data

    res = client.get("/opds/v2/recent.json")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/opds+json"
    data = res.get_json()
    assert "publications" in data
    assert len(data["publications"]) >= 1
    pub = data["publications"][0]
    prog_link = next(
        link
        for link in pub["links"]
        if link["rel"] == "http://opds-spec.org/progression"
    )
    assert prog_link["type"] == "application/opds-progression+json"
