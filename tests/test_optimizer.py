import io
import zipfile
from pathlib import Path

from PIL import Image

from aarkib.services.optimizer import (
    DEVICE_PRESETS,
    clean_css_content,
    get_preset,
    optimize_epub,
    optimize_image,
)
from aarkib.services.scanner import index_single_book


def test_presets_configuration():
    assert "x3" in DEVICE_PRESETS
    assert "x4" in DEVICE_PRESETS
    assert "kindle" in DEVICE_PRESETS
    assert "kobo" in DEVICE_PRESETS
    assert "eink" in DEVICE_PRESETS
    assert get_preset("x4")["max_width"] == 480
    assert get_preset("x4")["max_height"] == 800
    assert get_preset("x3")["max_width"] == 528
    assert get_preset("x3")["max_height"] == 792
    assert get_preset("unknown_device")["name"] == "Generic E-Ink"


def test_optimize_image():
    # Create large 2000x2000 RGB image
    img = Image.new("RGB", (2000, 2000), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    orig_bytes = buf.getvalue()

    # Optimize for Xteink X4 (max 480x800, grayscale, dithered)
    opt_bytes = optimize_image(
        orig_bytes,
        max_width=480,
        max_height=800,
        grayscale=True,
        dither=True,
        quality=75,
    )

    with Image.open(io.BytesIO(opt_bytes)) as opt_img:
        assert opt_img.size[0] <= 480
        assert opt_img.size[1] <= 800
        assert opt_img.mode in ("L", "RGB")


def test_clean_css_content():
    sample_css = """
    @font-face {
        font-family: 'CustomFont';
        src: url('fonts/custom.ttf');
    }
    body {
        font-family: 'CustomFont', sans-serif;
        color: #000000;
        background-color: white;
        margin: 20px;
    }
    p {
        font-size: 1.1em;
    }
    """
    cleaned = clean_css_content(sample_css, strip_fonts=True)
    assert "@font-face" not in cleaned
    assert "CustomFont" not in cleaned
    assert "font-size: 1.1em" in cleaned


def test_optimize_epub_end_to_end(tmp_path):
    # Build a sample rich EPUB with fonts, large images, and CSS
    epub_src = tmp_path / "rich_book.epub"
    epub_opt = tmp_path / "rich_book_opt.epub"

    img = Image.new("RGB", (1600, 2400), color="red")
    img_buf = io.BytesIO()
    img.save(img_buf, format="JPEG")
    img_bytes = img_buf.getvalue()

    container_xml = """<?xml version="1.0"?>
    <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
        <rootfiles>
            <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
        </rootfiles>
    </container>"""

    content_opf = """<?xml version="1.0" encoding="utf-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
        <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:title>Rich Book</dc:title>
            <dc:creator>Author</dc:creator>
        </metadata>
        <manifest>
            <item id="font1" href="fonts/myfont.ttf" media-type="application/x-font-ttf"/>
            <item id="img1" href="images/cover.jpg" media-type="image/jpeg"/>
            <item id="style" href="styles.css" media-type="text/css"/>
            <item id="ch1" href="ch1.html" media-type="application/xhtml+xml"/>
        </manifest>
        <spine>
            <itemref idref="ch1"/>
        </spine>
    </package>"""

    with zipfile.ZipFile(epub_src, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/fonts/myfont.ttf", b"FAKE_FONT_DATA_1234567890")
        zf.writestr("OEBPS/images/cover.jpg", img_bytes)
        zf.writestr(
            "OEBPS/styles.css",
            "@font-face { font-family: 'myfont'; src: url('fonts/myfont.ttf'); } body { font-family: myfont; }",
        )
        zf.writestr(
            "OEBPS/ch1.html",
            "<html><head><link rel='stylesheet' href='styles.css'/></head><body><h1>Chapter 1</h1><img src='images/cover.jpg'/></body></html>",
        )

    # Run optimizer for Xteink X4
    res_path = optimize_epub(epub_src, epub_opt, preset_key="x4")
    assert res_path.exists()
    assert res_path == epub_opt

    # Inspect optimized EPUB
    with zipfile.ZipFile(epub_opt, "r") as zf:
        names = zf.namelist()
        # 1. Fonts must be stripped
        assert "OEBPS/fonts/myfont.ttf" not in names
        assert not any(n.endswith(".ttf") for n in names)

        # 2. OPF must have font removed
        opf_str = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "myfont.ttf" not in opf_str

        # 3. CSS must have @font-face stripped
        css_str = zf.read("OEBPS/styles.css").decode("utf-8")
        assert "@font-face" not in css_str

        # 4. Images must be downscaled to <= 480x800
        img_data = zf.read("OEBPS/images/cover.jpg")
        with Image.open(io.BytesIO(img_data)) as opt_img:
            assert opt_img.size[0] <= 480
            assert opt_img.size[1] <= 800

    # Ensure source file is untouched
    assert epub_src.exists()
    with zipfile.ZipFile(epub_src, "r") as zf:
        assert "OEBPS/fonts/myfont.ttf" in zf.namelist()


def test_api_download_optimized(client, app, sample_epub, tmp_path):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        book_id = book.id

    # Test presets endpoint
    presets_res = client.get("/api/optimizer/presets")
    assert presets_res.status_code == 200
    data = presets_res.get_json()
    assert "x4" in data
    assert "x3" in data

    # Test download optimized route for Xteink X4
    res_x4 = client.get(f"/api/books/{book_id}/download/optimized/x4")
    assert res_x4.status_code == 200
    assert "application/epub+zip" in res_x4.headers["Content-Type"]
    assert "attachment" in res_x4.headers.get("Content-Disposition", "")
    assert "X4" in res_x4.headers.get("Content-Disposition", "")

    # Test download with query param preset=x3
    res_x3 = client.get(f"/api/books/{book_id}/download?preset=x3")
    assert res_x3.status_code == 200
    assert "X3" in res_x3.headers.get("Content-Disposition", "")

    # Test precompute optimize API
    opt_api_res = client.post(
        f"/api/books/{book_id}/optimize", json={"preset": "kindle"}
    )
    assert opt_api_res.status_code == 200
    opt_data = opt_api_res.get_json()
    assert opt_data["status"] == "success"
    assert opt_data["preset"] == "kindle"
    assert "optimized_size" in opt_data


def test_opds_preset_feeds(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        book_id = book.id

    # 1. OPDS 1.2 X4 Root Catalog
    res_root = client.get("/opds/x4")
    assert res_root.status_code == 200
    assert b"X4" in res_root.data
    assert b"/opds/x4/recent" in res_root.data

    # 2. OPDS 1.2 X4 Recent Feed -> acquisition links must point to /api/books/{id}/download/optimized/x4
    res_recent = client.get("/opds/x4/recent")
    assert res_recent.status_code == 200
    assert f"/api/books/{book_id}/download/optimized/x4".encode() in res_recent.data

    # 3. OPDS 1.2 X3 Feed
    res_x3 = client.get("/opds/x3/recent")
    assert res_x3.status_code == 200
    assert f"/api/books/{book_id}/download/optimized/x3".encode() in res_x3.data

    # 4. OPDS 2.0 JSON X4 Feed
    res_v2 = client.get("/opds/x4/v2/recent.json")
    assert res_v2.status_code == 200
    v2_data = res_v2.get_json()
    assert len(v2_data["publications"]) > 0
    acq_link = next(
        link
        for link in v2_data["publications"][0]["links"]
        if link["rel"] == "http://opds-spec.org/acquisition"
    )
    assert "/api/books/" in acq_link["href"]
    assert "/download/optimized/x4" in acq_link["href"]
