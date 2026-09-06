from __future__ import annotations

from pathlib import Path

from buukuu.models import Book
from buukuu.plugins import (
    BookMediaPlugin,
    MediaPlugin,
    PluginRegistry,
    init_plugins,
    plugin_registry,
)
from buukuu.services.parsers.base import BaseParsedMetadata


def test_book_media_plugin_metadata(sample_epub, sample_cbz):
    plugin = BookMediaPlugin()
    assert plugin.name == "books"
    assert plugin.media_type == "book"
    assert ".epub" in plugin.supported_extensions
    assert ".cbz" in plugin.supported_extensions

    # Test EPUB parsing
    epub_meta = plugin.parse_metadata(sample_epub)
    assert epub_meta is not None
    assert epub_meta.title == "Sample Test Book"
    assert "Jane Doe" in epub_meta.creators
    assert epub_meta.file_format == "epub"

    # Test CBZ parsing
    cbz_meta = plugin.parse_metadata(sample_cbz)
    assert cbz_meta is not None
    assert cbz_meta.title == "Episode 1: The Beginning"
    assert cbz_meta.file_format == "cbz"
    assert cbz_meta.media_type == "comic"

    # Test player URLs
    assert plugin.get_player_url(42, "epub") == "/reader/epub/42"
    assert plugin.get_player_url(42, "cbz") == "/reader/cbz/42"
    assert plugin.get_player_url(42, "zip") == "/reader/cbz/42"

    # Test health check
    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["plugin"] == "books"


def test_plugin_registry_and_extensions():
    reg = PluginRegistry()
    plugin = BookMediaPlugin()
    reg.register(plugin)

    assert reg.get_plugin("books") is plugin
    assert reg.get_plugin_for_extension(".epub") is plugin
    assert reg.get_plugin_for_extension("epub") is plugin
    assert reg.get_plugin_for_extension(".cbz") is plugin
    assert reg.get_plugin_for_extension(".xyz") is None
    assert reg.get_plugin_for_media_type("book") is plugin
    assert ".epub" in reg.get_all_supported_extensions()

    # Test unregister
    reg.unregister("books")
    assert reg.get_plugin("books") is None
    assert reg.get_plugin_for_extension(".epub") is None


def test_custom_media_plugin(tmp_path: Path):
    reg = PluginRegistry()

    class AudioMediaPlugin(MediaPlugin):
        name = "audio"
        media_type = "audio"
        supported_extensions = {".mp3", ".flac"}

        def parse_metadata(self, file_path: Path) -> BaseParsedMetadata:
            return BaseParsedMetadata(
                title=file_path.stem.title(),
                creators=["Audio Artist"],
                media_type="audio",
                file_format="mp3",
            )

        def extract_cover(self, file_path: Path) -> bytes | None:
            return b"fake_audio_art"

        def get_player_url(self, item_id: int, file_format: str | None = None) -> str:
            return f"/player/audio/{item_id}"

    audio_plugin = AudioMediaPlugin()
    reg.register(audio_plugin)

    assert reg.get_plugin_for_extension(".mp3") is audio_plugin
    assert reg.get_plugin_for_media_type("audio") is audio_plugin

    fake_file = tmp_path / "track1.mp3"
    fake_file.write_bytes(b"data")

    meta = audio_plugin.parse_metadata(fake_file)
    assert meta.title == "Track1"
    assert meta.media_type == "audio"
    assert audio_plugin.get_player_url(99, "mp3") == "/player/audio/99"


def test_book_model_player_url(app):
    book_epub = Book(
        title="Test Reader URL EPUB",
        original_file_path="/tmp/reader_test.epub",
        file_format="epub",
        file_hash="reader1",
    )
    book_cbz = Book(
        title="Test Reader URL CBZ",
        original_file_path="/tmp/reader_test.cbz",
        file_format="cbz",
        file_hash="reader2",
    )
    book_epub.id = 101
    book_cbz.id = 102

    assert book_epub.player_url == "/reader/epub/101"
    assert book_epub.reader_url == "/reader/epub/101"
    assert book_cbz.player_url == "/reader/cbz/102"
    assert book_cbz.reader_url == "/reader/cbz/102"


def test_init_plugins_with_app(app):
    reg = init_plugins(app)
    assert reg is plugin_registry
    assert reg.get_plugin("books") is not None
    assert "reader" in app.blueprints
