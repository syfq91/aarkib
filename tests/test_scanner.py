from pathlib import Path

from buukuu import create_app
from buukuu.config import TestConfig
from buukuu.services.scanner import get_library_dirs, scan_library


def test_get_library_dirs(tmp_path):
    class Config1(TestConfig):
        LIBRARY_DIR = f"{tmp_path}/dir1:{tmp_path}/dir2"

    app1 = create_app(Config1)
    dirs1 = get_library_dirs(app1)
    assert len(dirs1) == 2
    assert dirs1[0] == Path(f"{tmp_path}/dir1")
    assert dirs1[1] == Path(f"{tmp_path}/dir2")

    class Config2(TestConfig):
        LIBRARY_DIR = [tmp_path / "a", tmp_path / "b"]

    app2 = create_app(Config2)
    dirs2 = get_library_dirs(app2)
    assert len(dirs2) == 2
    assert dirs2[0] == tmp_path / "a"
    assert dirs2[1] == tmp_path / "b"


def test_scan_multiple_directories(tmp_path, sample_epub, sample_cbz):
    dir1 = tmp_path / "library1"
    dir2 = tmp_path / "library2"
    dir1.mkdir()
    dir2.mkdir()

    # Place sample books in two different directories
    import shutil

    shutil.copy(sample_epub, dir1 / "book1.epub")
    shutil.copy(sample_cbz, dir2 / "comic1.cbz")

    class MultiDirConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        LIBRARY_DIR = f"{dir1}:{dir2}"
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/multi_test.db"

    app = create_app(MultiDirConfig)
    res = scan_library(app)
    assert res["scanned"] == 2
    assert res["added_or_updated"] == 2


def test_scan_symlinked_directory(tmp_path, sample_epub):
    lib_dir = tmp_path / "main_library"
    external_dir = tmp_path / "external_books"
    lib_dir.mkdir()
    external_dir.mkdir()

    import shutil

    shutil.copy(sample_epub, external_dir / "external_book.epub")

    # Create symlink inside lib_dir pointing to external_dir
    symlink_dir = lib_dir / "linked_manga"
    symlink_dir.symlink_to(external_dir)

    class SymlinkConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        LIBRARY_DIR = lib_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/symlink_test.db"

    app = create_app(SymlinkConfig)
    res = scan_library(app)
    assert res["scanned"] == 1
    assert res["added_or_updated"] == 1
