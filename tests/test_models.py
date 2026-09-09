from aarkib.extensions import db
from aarkib.models import Author, Book, Series, Tag, User


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
    from aarkib.models import UserProgress

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

    from aarkib import migrate_database

    # Drop a column by recreating user_progress table without device_id
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS user_progress"))
        conn.execute(
            text(
                "CREATE TABLE user_progress ("
                "id INTEGER PRIMARY KEY, "
                "user_id INTEGER, "
                "media_item_id INTEGER NOT NULL, "
                "progress_location VARCHAR(500) NOT NULL, "
                "percentage FLOAT NOT NULL, "
                "is_completed BOOLEAN NOT NULL, "
                "last_accessed_at DATETIME NOT NULL)"
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


def test_generalized_media_model(app):
    from aarkib.models import Collection, Creator, MediaType

    # Check aliases
    assert Creator is Author
    assert Collection is Series

    # Check Book with default media_type for epub
    epub_book = Book(
        title="EPUB Title",
        original_file_path="/tmp/epub_title.epub",
        file_format="epub",
        file_hash="hash_epub_1",
        file_size=1048576,  # 1 MB
    )
    assert epub_book.media_type == MediaType.BOOK.value
    assert epub_book.is_book is True
    assert epub_book.is_comic is False
    assert epub_book.is_audio is False
    assert epub_book.is_video is False
    assert "1.0 MB" in epub_book.formatted_file_size

    # Check Book with cbz comic format
    cbz_book = Book(
        title="Comic Title",
        original_file_path="/tmp/comic_title.cbz",
        file_format="cbz",
        file_hash="hash_cbz_1",
    )
    assert cbz_book.media_type == MediaType.COMIC.value
    assert cbz_book.is_comic is True
    assert cbz_book.is_book is False

    # Check creator alias
    author = Creator(name="Test Creator")
    epub_book.authors.append(author)
    db.session.add_all([author, epub_book, cbz_book])
    db.session.commit()

    saved = db.session.get(Book, epub_book.id)
    assert saved.creators_display == "Test Creator"
    assert saved.authors_display == "Test Creator"


def test_generalized_progress_and_bookmarks(app):
    from aarkib.models import Bookmark, UserProgress

    book = Book(
        title="Progress Test Book",
        original_file_path="/tmp/prog.epub",
        file_format="epub",
        file_hash="prog1234",
    )
    db.session.add(book)
    db.session.commit()

    prog = UserProgress(
        book_id=book.id,
        progress_location="100.5",
        percentage=50.0,
    )
    assert prog.media_id == book.id
    prog.media_id = 999
    assert prog.book_id == 999
    prog.book_id = book.id

    bm = Bookmark(
        book_id=book.id,
        location="ch1.xhtml",
        title="Chapter 1 Bookmark",
    )
    assert bm.media_id == book.id
    bm.media_id = 888
    assert bm.book_id == 888


def test_parser_registry_and_base_metadata(tmp_path):
    from pathlib import Path

    from aarkib.services.parsers.base import (
        BaseParsedMetadata,
        ParsedBookMetadata,
        extract_metadata_from_file,
        register_parser,
    )

    # Test BaseParsedMetadata dataclass
    base_meta = BaseParsedMetadata(
        title="Audio Track",
        creators=["Artist One"],
        media_type="audio",
        file_format="mp3",
    )
    assert base_meta.title == "Audio Track"
    assert base_meta.media_type == "audio"
    assert "Artist One" in base_meta.creators

    # Test ParsedBookMetadata creator/author sync
    book_meta = ParsedBookMetadata(
        title="Sync Test",
        creators=["Synced Author"],
    )
    assert book_meta.authors == ["Synced Author"]

    # Test registering a mock custom parser (e.g. for audio)
    fake_audio = tmp_path / "song.mp3"
    fake_audio.write_bytes(b"ID3mock")

    def mock_audio_parser(path: Path) -> BaseParsedMetadata:
        return BaseParsedMetadata(
            title=path.stem.title(),
            creators=["Test Musician"],
            media_type="audio",
            file_format="mp3",
        )

    register_parser(".mp3", mock_audio_parser)
    extracted = extract_metadata_from_file(fake_audio)
    assert extracted is not None
    assert extracted.title == "Song"
    assert extracted.media_type == "audio"
    assert extracted.creators == ["Test Musician"]


def test_media_item_first_class_model(app):
    from aarkib.models import (
        Author,
        Book,
        Bookmark,
        Collection,
        Creator,
        Item,
        MediaItem,
        MediaType,
        Series,
        Tag,
        UserProgress,
    )
    from aarkib.services.media_service import edit_media_metadata

    # 1. Verify identity and alias equivalence
    assert MediaItem is Book
    assert MediaItem is Item
    assert Creator is Author
    assert Collection is Series

    # 2. Audio track item instantiation and audio mixin attributes
    audio = MediaItem(
        title="Midnight Symphony",
        original_file_path="/tmp/audio/symphony.flac",
        file_format="flac",
        file_hash="flac_hash_001",
        album="Classical Nights",
        track_number=4,
        disc_number=1,
        duration=365.0,
        bitrate=920,
    )
    assert audio.media_type == MediaType.AUDIO.value
    assert audio.is_audio is True
    assert audio.is_video is False
    assert audio.is_book is False
    assert audio.is_comic is False
    assert audio.formatted_duration == "6m 05s"
    assert repr(audio) == "<MediaItem None: Midnight Symphony>"

    # 3. Creator, Series, Tag synonyms
    creator = Creator(name="Beethoven")
    collection = Collection(name="The Masterpieces")
    genre = Tag(name="Classical")

    audio.creators.append(creator)
    audio.collection = collection
    audio.tags.append(genre)

    db.session.add_all([creator, collection, genre, audio])
    db.session.commit()

    # Verify bidirectional relationship synonyms
    assert audio in creator.media_items
    assert audio in creator.books
    assert audio in collection.media_items
    assert audio in collection.books
    assert audio in genre.media_items
    assert audio in genre.books

    # Verify progress and bookmark synonyms
    progress = UserProgress(
        media_item=audio,
        progress_location="180",
        percentage=50.0,
    )
    bookmark = Bookmark(
        media_item=audio,
        location="180",
        title="Interlude",
    )
    db.session.add_all([progress, bookmark])
    db.session.commit()

    assert progress.item is audio
    assert progress.book is audio
    assert bookmark.item is audio
    assert bookmark.book is audio
    assert progress in audio.progress_records
    assert bookmark in audio.bookmarks

    # 4. Test edit_media_metadata with audio & video fields
    edit_media_metadata(
        audio,
        {
            "title": "Midnight Symphony (Remastered)",
            "album": "Classical Nights Deluxe",
            "track_number": "5",
            "disc_number": "2",
            "season": "1",
            "episode": "2",
        },
    )
    db.session.commit()

    saved = db.session.get(MediaItem, audio.id)
    assert saved is not None
    assert saved.title == "Midnight Symphony (Remastered)"
    assert saved.album == "Classical Nights Deluxe"
    assert saved.track_number == 5
    assert saved.disc_number == 2
    assert saved.season == 1
    assert saved.episode == 2
    assert saved.episode_code == "S01E02"
