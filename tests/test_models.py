from buukuu.extensions import db
from buukuu.models import Author, Book, Series, Tag, User


def test_user_password_hashing(app):
    user = User(username="reader1")
    user.set_password("secret123")
    assert user.password_hash != "secret123"
    assert user.check_password("secret123") is True
    assert user.check_password("wrongpass") is False


def test_book_relations(app):
    author = Author(name="Arthur Conan Doyle")
    series = Series(name="Sherlock Holmes")
    tag = Tag(name="Mystery")

    book = Book(
        title="A Study in Scarlet",
        original_file_path="/tmp/study.epub",
        file_format="epub",
        file_hash="dummyhash123",
        series=series,
        series_index=1.0,
    )
    book.authors.append(author)
    book.tags.append(tag)

    db.session.add_all([author, series, tag, book])
    db.session.commit()

    saved_book = db.session.get(Book, book.id)
    assert saved_book is not None
    assert saved_book.authors_display == "Arthur Conan Doyle"
    assert saved_book.series.name == "Sherlock Holmes"
    assert "Mystery" in saved_book.tags_display


def test_user_progress_fields(app):
    from buukuu.models import UserProgress

    book = Book(
        title="Test Book",
        original_file_path="/tmp/test.epub",
        file_format="epub",
        file_hash="test1234",
    )
    db.session.add(book)
    db.session.commit()

    progress = UserProgress(
        book_id=book.id,
        percentage=45.5,
        device_id="urn:uuid:test-device-123",
        device_name="Kobo Clara 2E",
        chapter_title="Chapter 3",
        references_json='[{"href": "chapter3.xhtml"}]',
    )
    db.session.add(progress)
    db.session.commit()

    saved = db.session.get(UserProgress, progress.id)
    assert saved is not None
    assert saved.device_id == "urn:uuid:test-device-123"
    assert saved.device_name == "Kobo Clara 2E"
    assert saved.chapter_title == "Chapter 3"
    assert saved.references_json == '[{"href": "chapter3.xhtml"}]'


def test_migrate_database_adds_missing_columns(app):
    from sqlalchemy import inspect, text

    from buukuu import migrate_database

    # Drop a column by recreating user_progress table without device_id
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS user_progress"))
        conn.execute(
            text(
                "CREATE TABLE user_progress ("
                "id INTEGER PRIMARY KEY, "
                "user_id INTEGER, "
                "book_id INTEGER NOT NULL, "
                "progress_location VARCHAR(500) NOT NULL, "
                "percentage FLOAT NOT NULL, "
                "is_completed BOOLEAN NOT NULL, "
                "last_read_at DATETIME NOT NULL)"
            )
        )

    inspector = inspect(db.engine)
    cols_before = {c["name"] for c in inspector.get_columns("user_progress")}
    assert "device_id" not in cols_before

    # Run migration
    migrate_database()

    inspector = inspect(db.engine)
    cols_after = {c["name"] for c in inspector.get_columns("user_progress")}
    assert "device_id" in cols_after
    assert "device_name" in cols_after
    assert "chapter_title" in cols_after
    assert "references_json" in cols_after
