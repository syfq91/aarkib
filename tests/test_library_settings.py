from __future__ import annotations

from pathlib import Path

from aarkib.models import Library


def test_library_model_settings():
    """Test Library model settings property and helpers."""
    lib = Library(
        slug="test-lib",
        name="Test Library",
        path="/media/test",
        media_type="comic",
    )
    assert lib.settings == {}
    assert lib.auto_enrich is None
    assert lib.metadata_provider is None

    lib.set_setting("auto_enrich", False)
    lib.set_setting("metadata_provider", "comicvine")
    lib.set_setting("language", "ja")

    assert lib.auto_enrich is False
    assert lib.metadata_provider == "comicvine"
    assert lib.language == "ja"
    assert lib.settings == {
        "auto_enrich": False,
        "metadata_provider": "comicvine",
        "language": "ja",
    }
    d = lib.to_dict(count=5)
    assert d["settings"] == lib.settings


def test_library_api_settings_crud(app, client, tmp_path: Path):
    """Test creating and updating library settings via REST API."""
    target_dir = tmp_path / "custom_lib"
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create with settings
    post_payload = {
        "name": "Comics Archive",
        "path": str(target_dir),
        "media_type": "comic",
        "settings": {
            "auto_enrich": True,
            "metadata_provider": "comicvine",
        },
    }
    resp = client.post("/api/libraries", json=post_payload)
    assert resp.status_code == 201
    lib_data = resp.get_json()["library"]
    assert lib_data["settings"]["auto_enrich"] is True
    assert lib_data["settings"]["metadata_provider"] == "comicvine"
    lib_slug = lib_data["id"]

    # 2. Get library details
    get_resp = client.get(f"/api/libraries/{lib_slug}")
    assert get_resp.status_code == 200
    assert get_resp.get_json()["library"]["settings"]["auto_enrich"] is True

    # 3. Update settings via PUT
    put_payload = {
        "settings": {
            "auto_enrich": False,
            "metadata_provider": "openlibrary",
        }
    }
    put_resp = client.put(f"/api/libraries/{lib_slug}", json=put_payload)
    assert put_resp.status_code == 200
    updated = put_resp.get_json()["library"]
    assert updated["settings"]["auto_enrich"] is False
    assert updated["settings"]["metadata_provider"] == "openlibrary"
