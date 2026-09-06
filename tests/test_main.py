from aarkib import create_app
from aarkib.config import TestConfig


def test_create_app(tmp_path):
    class CustomConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        LIBRARY_DIR = tmp_path / "books"
        COVERS_DIR = tmp_path / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/app_test.db"

    app = create_app(CustomConfig)
    assert app is not None
    assert app.config["TESTING"] is True


def test_cli_commands(runner):
    result = runner.invoke(args=["init-db"])
    assert result.exit_code == 0
    assert "Initialized the database." in result.output
