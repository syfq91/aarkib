import pytest

from aarkib.extensions import db
from aarkib.models import User
from aarkib.models.setting import SystemSetting
from aarkib.services.settings_service import (
    MANAGED_SETTINGS,
    get_effective_settings,
    load_settings_into_config,
    parse_setting_value,
    reset_settings_to_defaults,
    update_settings,
)


def _create_admin(app):
    from sqlalchemy import select

    with app.app_context():
        user = db.session.scalar(select(User).where(User.username == "admin_test"))
        if not user:
            user = User(username="admin_test", is_admin=True)
            user.set_password("adminpass")
            db.session.add(user)
            db.session.commit()
        return user.id


def _create_reader(app):
    from sqlalchemy import select

    with app.app_context():
        user = db.session.scalar(select(User).where(User.username == "reader_test"))
        if not user:
            user = User(username="reader_test", is_admin=False)
            user.set_password("readerpass")
            db.session.add(user)
            db.session.commit()
        return user.id


def _login_admin(client, app):
    client.get("/auth/logout")
    _create_admin(app)
    client.post(
        "/auth/login",
        data={"username": "admin_test", "password": "adminpass"},
        follow_redirects=True,
    )


def _login_reader(client, app):
    client.get("/auth/logout")
    _create_reader(app)
    client.post(
        "/auth/login",
        data={"username": "reader_test", "password": "readerpass"},
        follow_redirects=True,
    )


def test_system_setting_model(app):
    with app.app_context():
        setting = SystemSetting(key="TEST_KEY", value="test_val")
        db.session.add(setting)
        db.session.commit()

        retrieved = db.session.get(SystemSetting, "TEST_KEY")
        assert retrieved is not None
        assert retrieved.key == "TEST_KEY"
        assert retrieved.value == "test_val"
        assert "<SystemSetting" in repr(retrieved)
        d = retrieved.to_dict()
        assert d["key"] == "TEST_KEY"
        assert d["value"] == "test_val"
        assert d["updated_at"] is not None


def test_parse_setting_value_validation():
    bool_spec = MANAGED_SETTINGS["AUTO_SCAN_ON_START"]
    assert parse_setting_value(bool_spec, True) is True
    assert parse_setting_value(bool_spec, False) is False
    assert parse_setting_value(bool_spec, "true") is True
    assert parse_setting_value(bool_spec, "1") is True
    assert parse_setting_value(bool_spec, "false") is False
    assert parse_setting_value(bool_spec, "0") is False

    int_spec = MANAGED_SETTINGS["PAGE_SIZE"]
    assert parse_setting_value(int_spec, 24) == 24
    assert parse_setting_value(int_spec, "48") == 48

    with pytest.raises(ValueError, match="valid integer"):
        parse_setting_value(int_spec, "not_an_int")

    with pytest.raises(ValueError, match="at least"):
        parse_setting_value(int_spec, 2)

    with pytest.raises(ValueError, match="cannot exceed"):
        parse_setting_value(int_spec, 9999)

    provider_spec = MANAGED_SETTINGS["METADATA_PROVIDER"]
    assert parse_setting_value(provider_spec, "all") == "all"
    assert parse_setting_value(provider_spec, "googlebooks") == "googlebooks"

    with pytest.raises(ValueError, match="must be one of"):
        parse_setting_value(provider_spec, "invalid_provider")


def test_settings_service_lifecycle(app):
    with app.app_context():
        effective = get_effective_settings(app)
        assert "AUTO_SCAN_ON_START" in effective["settings"]
        assert "PAGE_SIZE" in effective["settings"]
        assert effective["settings"]["AUTO_SCAN_ON_START"]["default_value"] is True
        assert effective["settings"]["PAGE_SIZE"]["default_value"] == 24
        assert effective["settings"]["AUTO_ENRICH"]["default_value"] is False
        assert effective["settings"]["METADATA_PROVIDER"]["default_value"] == "all"
        assert effective["settings"]["WATCH_LIBRARY"]["default_value"] is True

        # Update settings via service
        updated = update_settings(
            app,
            {
                "AUTO_SCAN_ON_START": False,
                "PAGE_SIZE": 48,
                "METADATA_PROVIDER": "googlebooks",
            },
        )
        assert app.config["AUTO_SCAN_ON_START"] is False
        assert app.config["PAGE_SIZE"] == 48
        assert app.config["METADATA_PROVIDER"] == "googlebooks"
        assert updated["settings"]["AUTO_SCAN_ON_START"]["is_overridden"] is True
        assert updated["settings"]["AUTO_SCAN_ON_START"]["value"] is False

        # Load settings in a simulated restart
        app.config["AUTO_SCAN_ON_START"] = True  # reset in-memory config
        load_settings_into_config(app)
        assert app.config["AUTO_SCAN_ON_START"] is False  # restored from DB

        # Reset to defaults
        reset = reset_settings_to_defaults(app)
        assert reset["settings"]["AUTO_SCAN_ON_START"]["is_overridden"] is False
        assert app.config["PAGE_SIZE"] == 24


def test_api_settings_permissions(client, app):
    # Unauthenticated request to /api/settings
    client.get("/auth/logout")
    res = client.get("/api/settings")
    assert res.status_code in (302, 401, 403)

    # Reader user request
    _login_reader(client, app)
    res = client.get("/api/settings")
    assert res.status_code == 403

    # Admin user request
    client.get("/auth/logout")
    _login_admin(client, app)
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert "AUTO_SCAN_ON_START" in data["settings"]
    assert "PAGE_SIZE" in data["settings"]


def test_api_settings_update_and_reset(client, app):
    _login_admin(client, app)

    # Invalid payload
    res_bad = client.patch(
        "/api/settings",
        json={"PAGE_SIZE": "invalid_number"},
    )
    assert res_bad.status_code == 400
    assert "error" in res_bad.get_json()

    # Valid update
    res = client.patch(
        "/api/settings",
        json={
            "PAGE_SIZE": 36,
            "AUTO_ENRICH": True,
            "METADATA_PROVIDER": "openlibrary",
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert app.config["PAGE_SIZE"] == 36
    assert app.config["AUTO_ENRICH"] is True
    assert app.config["METADATA_PROVIDER"] == "openlibrary"

    # Reset endpoint
    res_reset = client.post("/api/settings/reset")
    assert res_reset.status_code == 200
    reset_data = res_reset.get_json()
    assert reset_data["status"] == "success"
    assert app.config["PAGE_SIZE"] == 24
    assert app.config["AUTO_ENRICH"] is False


def test_ui_settings_view_for_admin_and_reader(client, app):
    # Reader viewing /settings should see libraries and not admin preferences
    _login_reader(client, app)
    res_reader = client.get("/settings")
    assert res_reader.status_code == 200
    assert b"System & Library Preferences" not in res_reader.data
    assert (
        b"Media Folders &amp; Libraries" in res_reader.data
        or b"Media Folders & Libraries" in res_reader.data
    )

    # Reader attempting to access admin categories should be redirected
    res_sys_denied = client.get("/settings/system")
    assert res_sys_denied.status_code == 302
    assert "/settings/libraries" in res_sys_denied.headers["Location"]

    res_plug_denied = client.get("/settings/plugins")
    assert res_plug_denied.status_code == 302

    res_user_denied = client.get("/settings/users")
    assert res_user_denied.status_code == 302

    # Reader can access libraries and integrations
    res_lib = client.get("/settings/libraries")
    assert res_lib.status_code == 200

    res_integ = client.get("/settings/integrations")
    assert res_integ.status_code == 200
    assert b"OPDS Catalog Feeds" in res_integ.data

    # Admin viewing /settings and /settings/system should see system preferences form
    client.get("/auth/logout")
    _login_admin(client, app)

    res_admin = client.get("/settings")
    assert res_admin.status_code == 200
    assert (
        b"System &amp; Library Preferences" in res_admin.data
        or b"System & Library Preferences" in res_admin.data
    )
    assert b"setting-AUTO_SCAN_ON_START" in res_admin.data
    assert b"setting-PAGE_SIZE" in res_admin.data

    res_system = client.get("/settings/system")
    assert res_system.status_code == 200
    assert b"setting-AUTO_SCAN_ON_START" in res_system.data
    assert b"Reset to Defaults" in res_system.data
    assert b"Reset to .env" not in res_system.data

    # Admin viewing /settings/plugins should see plugin cards and toggles
    res_plugins = client.get("/settings/plugins")
    assert res_plugins.status_code == 200
    assert b"Media Format Plugins" in res_plugins.data
    assert (
        b"Protocols &amp; Streaming Services" in res_plugins.data
        or b"Protocols & Streaming Services" in res_plugins.data
    )
    assert b"setting-ENABLE_OPDS" in res_plugins.data
    assert b"setting-ENABLE_SUBSONIC" in res_plugins.data
    assert b"setting-ENABLE_BOOKS" in res_plugins.data

    # Admin viewing /settings/users should see user management
    res_users = client.get("/settings/users")
    assert res_users.status_code == 200
    assert b"User Management" in res_users.data
    assert b"Add New User" in res_users.data

    # Admin viewing /settings/libraries
    res_libraries = client.get("/settings/libraries")
    assert res_libraries.status_code == 200
    assert b"Library Tools" in res_libraries.data

    # Invalid category returns 404
    res_404 = client.get("/settings/non_existent_category")
    assert res_404.status_code == 404


def test_plugin_settings_sync_and_api(client, app):
    from aarkib.plugins import plugin_registry

    _login_admin(client, app)

    # Verify all 9 plugin settings exist in MANAGED_SETTINGS
    plugin_keys = [
        "ENABLE_OPDS",
        "ENABLE_SUBSONIC",
        "ENABLE_EINK_OPTIMIZER",
        "ENABLE_BOOKS",
        "ENABLE_VIDEO",
        "ENABLE_AUDIO",
        "ENABLE_AUDIOBOOK",
        "ENABLE_PODCAST",
        "ENABLE_MUSIC",
    ]
    for key in plugin_keys:
        assert key in MANAGED_SETTINGS
        assert MANAGED_SETTINGS[key].type is bool

    # Initially enabled
    assert plugin_registry.get_plugin("opds").enabled is True
    assert plugin_registry.get_plugin("subsonic").enabled is True

    # Disable OPDS and Subsonic via PATCH /api/settings
    res = client.patch(
        "/api/settings",
        json={"ENABLE_OPDS": False, "ENABLE_SUBSONIC": False},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert app.config["ENABLE_OPDS"] is False
    assert app.config["ENABLE_SUBSONIC"] is False
    assert plugin_registry.get_plugin("opds").enabled is False
    assert plugin_registry.get_plugin("subsonic").enabled is False

    # Verify in-memory persistence and reload
    load_settings_into_config(app)
    assert app.config["ENABLE_OPDS"] is False
    assert plugin_registry.get_plugin("opds").enabled is False

    # Reset back to defaults
    res_reset = client.post("/api/settings/reset")
    assert res_reset.status_code == 200
    assert app.config["ENABLE_OPDS"] is True
    assert app.config["ENABLE_SUBSONIC"] is True
    assert plugin_registry.get_plugin("opds").enabled is True
    assert plugin_registry.get_plugin("subsonic").enabled is True


def test_runtime_settings_defaults_and_env_independence(monkeypatch):
    """Verifies that runtime settings rely on Python/Config defaults and do not read AARKIB_* env vars."""
    from aarkib.config import Config

    # Set arbitrary values in environment that previously configured these
    monkeypatch.setenv("AARKIB_AUTO_SCAN", "false")
    monkeypatch.setenv("AARKIB_WATCH_LIBRARY", "false")
    monkeypatch.setenv("AARKIB_AUTO_ENRICH", "true")
    monkeypatch.setenv("AARKIB_METADATA_PROVIDER", "googlebooks")
    monkeypatch.setenv("AARKIB_PAGE_SIZE", "99")

    # Config class defaults must remain untouched by environment variables
    cfg = Config()
    assert cfg.AUTO_SCAN_ON_START is True
    assert cfg.WATCH_LIBRARY is True
    assert cfg.AUTO_ENRICH is False
    assert cfg.METADATA_PROVIDER == "all"
    assert cfg.PAGE_SIZE == 24


def test_settings_integrations_hides_disabled_plugins(client, app):
    """Verifies that disabled plugins are not rendered on the integrations page,
    an empty state is shown when all are disabled, and cards reappear when re-enabled."""
    _login_admin(client, app)

    # 1. Default state: all 4 cards should be visible
    res = client.get("/settings/integrations")
    assert res.status_code == 200
    assert b"OPDS Catalog Feeds" in res.data
    assert b"Subsonic Mobile Streaming API" in res.data
    assert b"Jellyfin Client API" in res.data
    assert b"E-Ink Device Optimizer" in res.data
    assert b"No Integrations Enabled" not in res.data

    # 2. Disable OPDS only: OPDS card should disappear, others remain
    client.patch("/api/settings", json={"ENABLE_OPDS": False})
    res_no_opds = client.get("/settings/integrations")
    assert res_no_opds.status_code == 200
    assert b"OPDS Catalog Feeds" not in res_no_opds.data
    assert b"Subsonic Mobile Streaming API" in res_no_opds.data
    assert b"Jellyfin Client API" in res_no_opds.data
    assert b"E-Ink Device Optimizer" in res_no_opds.data
    assert b"No Integrations Enabled" not in res_no_opds.data

    # 3. Disable Subsonic and Jellyfin as well
    client.patch(
        "/api/settings", json={"ENABLE_SUBSONIC": False, "ENABLE_JELLYFIN": False}
    )
    res_only_eink = client.get("/settings/integrations")
    assert res_only_eink.status_code == 200
    assert b"OPDS Catalog Feeds" not in res_only_eink.data
    assert b"Subsonic Mobile Streaming API" not in res_only_eink.data
    assert b"Jellyfin Client API" not in res_only_eink.data
    assert b"E-Ink Device Optimizer" in res_only_eink.data
    assert b"No Integrations Enabled" not in res_only_eink.data

    # 4. Disable E-Ink Optimizer too: all 4 are now disabled -> empty state banner should show
    client.patch("/api/settings", json={"ENABLE_EINK_OPTIMIZER": False})
    res_all_disabled = client.get("/settings/integrations")
    assert res_all_disabled.status_code == 200
    assert b"OPDS Catalog Feeds" not in res_all_disabled.data
    assert b"Subsonic Mobile Streaming API" not in res_all_disabled.data
    assert b"Jellyfin Client API" not in res_all_disabled.data
    assert b"E-Ink Device Optimizer" not in res_all_disabled.data
    assert b"No Integrations Enabled" in res_all_disabled.data
    assert b"/settings/plugins" in res_all_disabled.data

    # 5. Non-admin reader viewing when all disabled should also see empty state (with reader message)
    _login_reader(client, app)
    res_reader_empty = client.get("/settings/integrations")
    assert res_reader_empty.status_code == 200
    assert b"No Integrations Enabled" in res_reader_empty.data
    assert b"Please contact an administrator" in res_reader_empty.data

    # 6. Re-enable OPDS via admin: OPDS card reappears, empty state disappears
    _login_admin(client, app)
    client.patch("/api/settings", json={"ENABLE_OPDS": True})
    res_re_opds = client.get("/settings/integrations")
    assert res_re_opds.status_code == 200
    assert b"OPDS Catalog Feeds" in res_re_opds.data
    assert b"Subsonic Mobile Streaming API" not in res_re_opds.data
    assert b"No Integrations Enabled" not in res_re_opds.data

    # Cleanup: Reset back to defaults
    client.post("/api/settings/reset")


def test_plugins_page_filter_ui(client, app):
    """Verifies that /settings/plugins renders the status filter controls,
    live count badges, and plugin cards with data-enabled metadata."""
    _login_admin(client, app)

    res = client.get("/settings/plugins")
    assert res.status_code == 200

    # Filter buttons exist
    assert b"plugin-filter-bar" in res.data
    assert b"filter-btn-all" in res.data
    assert b"filter-btn-enabled" in res.data
    assert b"filter-btn-disabled" in res.data

    # Count badges exist
    assert b"count-all" in res.data
    assert b"count-enabled" in res.data
    assert b"count-disabled" in res.data

    # Group sections and card metadata exist
    assert b"plugin-group-section" in res.data
    assert b"data-enabled=" in res.data
    assert b"setPluginFilter" in res.data
    assert b"plugin-filter-empty" in res.data


def test_api_key_settings_lifecycle(app):
    """Verifies that API keys are managed as secrets, masked in views, and dynamic."""
    with app.app_context():
        # Verify definitions
        tmdb_spec = MANAGED_SETTINGS["TMDB_API_KEY"]
        comicvine_spec = MANAGED_SETTINGS["COMICVINE_API_KEY"]
        podcast_spec = MANAGED_SETTINGS["PODCASTINDEX_API_KEY"]
        assert tmdb_spec.is_secret is True
        assert comicvine_spec.is_secret is True
        assert podcast_spec.is_secret is True

        effective_init = get_effective_settings(app)
        assert effective_init["settings"]["TMDB_API_KEY"]["is_secret"] is True
        assert effective_init["settings"]["TMDB_API_KEY"]["is_set"] is False

        # Update API key
        update_settings(
            app,
            {
                "TMDB_API_KEY": "secret_tmdb_token_12345",
                "COMICVINE_API_KEY": "secret_comicvine_key_9999",
            },
        )
        assert app.config["TMDB_API_KEY"] == "secret_tmdb_token_12345"
        assert app.config["COMICVINE_API_KEY"] == "secret_comicvine_key_9999"

        # Check effective settings mask the value
        effective_after = get_effective_settings(app)
        tmdb_info = effective_after["settings"]["TMDB_API_KEY"]
        assert tmdb_info["is_set"] is True
        assert "2345" in tmdb_info["value"]
        assert tmdb_info["value"].startswith("••••••••")
        assert "secret_tmdb_token_12345" not in tmdb_info["value"]
        assert effective_after["values"]["TMDB_API_KEY"] == tmdb_info["value"]

        # Sending masked placeholder back does not overwrite existing key
        update_settings(app, {"TMDB_API_KEY": tmdb_info["value"]})
        assert app.config["TMDB_API_KEY"] == "secret_tmdb_token_12345"

        # Explicitly clearing with empty string works
        update_settings(app, {"TMDB_API_KEY": ""})
        assert app.config["TMDB_API_KEY"] == ""
        effective_cleared = get_effective_settings(app)
        assert effective_cleared["settings"]["TMDB_API_KEY"]["is_set"] is False


def test_api_settings_api_keys_endpoint(client, app):
    """Verifies that admins can update plugin API keys via PATCH /api/settings."""
    _login_admin(client, app)

    res = client.patch(
        "/api/settings",
        json={
            "TMDB_API_KEY": "tmdb_api_key_test_val",
            "COMICVINE_API_KEY": "cv_api_key_test_val",
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert "_val" in data["settings"]["TMDB_API_KEY"]["value"]
    assert data["settings"]["TMDB_API_KEY"]["value"].startswith("••••••••")

    # Reader cannot access or modify
    _login_reader(client, app)
    res_reader = client.patch(
        "/api/settings",
        json={"TMDB_API_KEY": "hacked"},
    )
    assert res_reader.status_code == 403

    # Reset
    _login_admin(client, app)
    client.post("/api/settings/reset")
