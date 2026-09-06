from pathlib import Path

from aarkib import create_app
from aarkib.config import TestConfig
from aarkib.services.scanner import get_library_dirs, scan_library


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


def test_get_env_library_dirs(tmp_path):
    from aarkib.config import get_env_library_dirs, split_path_string

    # Test split_path_string
    assert split_path_string("/a:/b;/c,/d\n/e") == ["/a", "/b", "/c", "/d", "/e"]
    assert split_path_string("C:\\books;D:\\comics") == ["C:\\books", "D:\\comics"]

    # Test numbered and named environment variables in custom dict
    mock_env = {
        "AARKIB_LIBRARY_DIR": f"{tmp_path}/main",
        "AARKIB_LIBRARY_DIR1": f"{tmp_path}/manga",
        "AARKIB_LIBRARY_DIR2": f"{tmp_path}/comics",
        "AARKIB_LIBRARY_DIR3": f"{tmp_path}/novels",
        "BUUKU_LIBRARY_DIR4": f"{tmp_path}/audiobooks",
        "AARKIB_DIR_LIGHTNOVELS": f"{tmp_path}/ln",
        "AARKIB_LIBRARY_DIR_10": f"{tmp_path}/extra10",
    }
    paths = get_env_library_dirs(env=mock_env)
    path_strs = [str(p) for p in paths]

    # Primary should come first
    assert path_strs[0] == str(tmp_path / "main")
    # Numbered matches: index 1, 2, 3, 4, 10
    assert str(tmp_path / "manga") in path_strs
    assert str(tmp_path / "comics") in path_strs
    assert str(tmp_path / "novels") in path_strs
    assert str(tmp_path / "audiobooks") in path_strs
    assert str(tmp_path / "extra10") in path_strs
    # Named match
    assert str(tmp_path / "ln") in path_strs
    # Ordering verification: index 1 < index 2 < index 3 < index 4 < index 10
    idx_manga = path_strs.index(str(tmp_path / "manga"))
    idx_comics = path_strs.index(str(tmp_path / "comics"))
    idx_novels = path_strs.index(str(tmp_path / "novels"))
    idx_audio = path_strs.index(str(tmp_path / "audiobooks"))
    idx_extra10 = path_strs.index(str(tmp_path / "extra10"))
    assert idx_manga < idx_comics < idx_novels < idx_audio < idx_extra10


def test_scan_multiple_directories_via_env(
    tmp_path, sample_epub, sample_cbz, monkeypatch
):
    dir1 = tmp_path / "folder_one"
    dir2 = tmp_path / "folder_two"
    dir3 = tmp_path / "folder_three"
    dir1.mkdir()
    dir2.mkdir()
    dir3.mkdir()

    import shutil

    shutil.copy(sample_epub, dir1 / "book1.epub")
    shutil.copy(sample_cbz, dir2 / "comic1.cbz")
    shutil.copy(sample_epub, dir3 / "book2.epub")

    monkeypatch.setenv("BUUKUU_LIBRARY_DIR", str(dir1))
    monkeypatch.setenv("BUUKUU_LIBRARY_DIR1", str(dir2))
    monkeypatch.setenv("BUUKUU_LIBRARY_DIR2", str(dir3))

    class EnvMultiDirConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/env_multi_test.db"

    app = create_app(EnvMultiDirConfig)
    dirs = get_library_dirs(app)
    assert len(dirs) == 3
    assert dir1 in dirs
    assert dir2 in dirs
    assert dir3 in dirs

    res = scan_library(app)
    assert res["scanned"] == 3
    assert res["added_or_updated"] == 3
