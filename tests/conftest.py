import io
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from aarkib import create_app
from aarkib.config import TestConfig
from aarkib.extensions import db


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    class CustomTestConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIR = tmp_path / "media"
        MEDIA_DIRS = [tmp_path / "media"]
        COVERS_DIR = tmp_path / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/test.db"

    app = create_app(CustomTestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def unauth_client(app: Flask):
    """Provides a fresh, unauthenticated test client."""
    return app.test_client()


@pytest.fixture
def default_user(app: Flask):
    """Creates a default administrator user for test suite requests."""
    from sqlalchemy import select

    from aarkib.models import User

    with app.app_context():
        user = db.session.scalar(
            select(User).where(User.username == "default_test_admin")
        )
        if not user:
            user = User(username="default_test_admin", is_admin=True)
            user.set_password("defaultpass")
            db.session.add(user)
            db.session.commit()
        return user.id


@pytest.fixture
def client(app: Flask, default_user: int):
    """Provides a test client pre-authenticated as the default administrator."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(default_user)
        sess["_fresh"] = True
    return c


@pytest.fixture
def sample_epub(tmp_path: Path) -> Path:
    epub_path = tmp_path / "sample.epub"
    container_xml = """<?xml version="1.0"?>
    <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
        <rootfiles>
            <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
        </rootfiles>
    </container>"""

    content_opf = """<?xml version="1.0" encoding="utf-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
        <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:title>Sample Test Book</dc:title>
            <dc:creator>Jane Doe</dc:creator>
            <dc:description>A sample book for automated testing.</dc:description>
            <dc:language>en</dc:language>
            <dc:identifier scheme="ISBN">978-3-16-148410-0</dc:identifier>
            <dc:subject>Fiction</dc:subject>
        </metadata>
        <manifest>
            <item id="chapter1" href="chapter1.html" media-type="application/xhtml+xml"/>
        </manifest>
        <spine>
            <itemref idref="chapter1"/>
        </spine>
    </package>"""

    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr(
            "OEBPS/chapter1.html", "<html><body><h1>Hello World</h1></body></html>"
        )

    return epub_path


@pytest.fixture
def sample_cbz(tmp_path: Path) -> Path:
    from PIL import Image

    cbz_path = tmp_path / "sample.cbz"

    # Create simple 10x10 image bytes
    img = Image.new("RGB", (10, 10), color="blue")
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="JPEG")
    img_bytes = img_byte_arr.getvalue()

    comic_info = """<?xml version="1.0"?>
    <ComicInfo>
        <Title>Episode 1: The Beginning</Title>
        <Series>Epic Adventure</Series>
        <Number>1</Number>
        <Writer>John Comics</Writer>
        <Genre>Action, Adventure</Genre>
    </ComicInfo>"""

    with zipfile.ZipFile(cbz_path, "w") as zf:
        zf.writestr("ComicInfo.xml", comic_info)
        zf.writestr("001.jpg", img_bytes)
        zf.writestr("002.jpg", img_bytes)

    return cbz_path
