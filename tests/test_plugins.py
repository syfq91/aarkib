from __future__ import annotations

from pathlib import Path

from aarkib.models import MediaItem
from aarkib.plugins import (
    BookMediaPlugin,
    MediaPlugin,
    PluginRegistry,
    init_plugins,
    plugin_registry,
)
from aarkib.services.parsers.base import BaseParsedMetadata


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


def test_media_item_model_player_url(app):
    book_epub = MediaItem(
        title="Test Reader URL EPUB",
        original_file_path="/tmp/reader_test.epub",
        file_format="epub",
        file_hash="reader1",
    )
    book_cbz = MediaItem(
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
    assert "opds" in app.blueprints
    assert "subsonic" in app.blueprints


def test_opds_protocol_plugin():
    from aarkib.plugins import OPDSProtocolPlugin

    plugin = OPDSProtocolPlugin()
    assert plugin.name == "opds"
    assert plugin.plugin_type == "protocol"
    assert plugin.protocol_version == "2.0"
    assert plugin.csrf_exempt is True
    assert plugin.blueprint_options.get("url_prefix") == "/opds"

    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["protocol_version"] == "2.0"
    assert "book" in health["supported_types"]


def test_subsonic_protocol_plugin():
    from aarkib.plugins import SubsonicProtocolPlugin

    plugin = SubsonicProtocolPlugin()
    assert plugin.name == "subsonic"
    assert plugin.plugin_type == "protocol"
    assert plugin.protocol_version == "1.16.1"
    assert plugin.csrf_exempt is True
    assert plugin.blueprint_options.get("url_prefix") == "/rest"

    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["protocol_version"] == "1.16.1"
    assert health["url_prefix"] == "/rest"


def test_jellyfin_protocol_plugin():
    from aarkib.plugins import JellyfinProtocolPlugin

    plugin = JellyfinProtocolPlugin()
    assert plugin.name == "jellyfin"
    assert plugin.plugin_type == "protocol"
    assert plugin.protocol_version == "10.9.11"
    assert plugin.csrf_exempt is True
    assert plugin.blueprint_options.get("url_prefix") == ""

    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["protocol_version"] == "10.9.11"


def test_eink_optimizer_plugin():
    from aarkib.plugins import EInkOptimizerPlugin

    plugin = EInkOptimizerPlugin()
    assert plugin.name == "eink_optimizer"
    assert plugin.plugin_type == "optimizer"
    assert ".epub" in plugin.supported_formats

    presets = plugin.get_presets()
    assert "x3" in presets
    assert "x4" in presets
    assert "kindle" in presets

    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["pil_available"] is True


def test_api_plugins_endpoint(client):
    response = client.get("/api/plugins")
    assert response.status_code == 200
    data = response.get_json()
    assert "plugins" in data
    plugin_names = [p["name"] for p in data["plugins"]]
    assert "books" in plugin_names
    assert "video" in plugin_names
    assert "audio" in plugin_names
    assert "opds" in plugin_names
    assert "subsonic" in plugin_names
    assert "jellyfin" in plugin_names
    assert "eink_optimizer" in plugin_names

    opds_info = next(p for p in data["plugins"] if p["name"] == "opds")
    assert opds_info["type"] == "protocol"
    assert opds_info["enabled"] is True
    assert opds_info["health"]["status"] == "ok"


def test_plugin_toggle_disable():
    from flask import Flask

    from aarkib.plugins import init_plugins

    test_app = Flask("test_toggle_app")
    test_app.config["AARKIB_ENABLE_SUBSONIC"] = False
    init_plugins(test_app)

    assert "subsonic" not in test_app.blueprints
    assert "opds" in test_app.blueprints


def test_runtime_opds_guard(client, app):
    from aarkib.services.settings_service import update_settings

    # Disable OPDS
    update_settings(app, {"ENABLE_OPDS": False})
    res = client.get("/opds")
    assert res.status_code == 404
    assert b"OPDS catalog feeds are disabled on this server." in res.data

    res_opt = client.get("/opds/x4")
    assert res_opt.status_code == 404

    # Re-enable OPDS
    update_settings(app, {"ENABLE_OPDS": True})
    res_enabled = client.get("/opds")
    assert res_enabled.status_code == 200
    assert b"feed" in res_enabled.data.lower()


def test_runtime_subsonic_guard(client, app):
    from aarkib.services.settings_service import update_settings

    # Disable Subsonic
    update_settings(app, {"ENABLE_SUBSONIC": False})

    # JSON request
    res_json = client.get("/rest/ping.view?f=json")
    assert res_json.status_code == 200
    data = res_json.get_json()
    assert data["subsonic-response"]["status"] == "failed"
    assert data["subsonic-response"]["error"]["code"] == 0
    assert "disabled" in data["subsonic-response"]["error"]["message"].lower()

    # XML request
    res_xml = client.get("/rest/ping.view?f=xml")
    assert res_xml.status_code == 200
    assert b'code="0"' in res_xml.data

    # Re-enable Subsonic
    update_settings(app, {"ENABLE_SUBSONIC": True})
    res_enabled = client.get("/rest/ping.view?f=json")
    assert res_enabled.status_code == 200
    data_enabled = res_enabled.get_json()
    # Code should now be auth required (40) or ok, not 10
    if data_enabled["subsonic-response"]["status"] == "failed":
        assert data_enabled["subsonic-response"]["error"]["code"] != 10


def test_plugin_supported_extensions_active_only():
    from aarkib.plugins import plugin_registry

    video_plugin = plugin_registry.get_plugin("video")
    if video_plugin:
        original_state = video_plugin.enabled
        try:
            video_plugin.enabled = True
            assert ".mp4" in plugin_registry.get_all_supported_extensions(
                active_only=True
            )

            video_plugin.enabled = False
            assert ".mp4" not in plugin_registry.get_all_supported_extensions(
                active_only=True
            )
            assert ".mp4" in plugin_registry.get_all_supported_extensions(
                active_only=False
            )
        finally:
            video_plugin.enabled = original_state
