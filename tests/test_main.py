from aarkib import create_app, main
from aarkib.config import TestConfig


def test_create_app(tmp_path):
    class CustomConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIR = tmp_path / "media"
        COVERS_DIR = tmp_path / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/app_test.db"

    app = create_app(CustomConfig)
    assert app is not None
    assert app.config["TESTING"] is True


def test_main_startup(monkeypatch):
    started = {}

    def fake_run(self, host="0.0.0.0", port=5000, debug=False):
        started["host"] = host
        started["port"] = port
        started["debug"] = debug

    monkeypatch.setattr("flask.Flask.run", fake_run)
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("AARKIB_DEBUG", "true")

    main()

    assert started["host"] == "127.0.0.1"
    assert started["port"] == 8080
    assert started["debug"] is True
