from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Device presets for e-ink optimization
DEVICE_PRESETS: dict[str, dict[str, Any]] = {
    "x3": {
        "name": "Xteink X3",
        "max_width": 600,
        "max_height": 800,
        "grayscale": True,
        "dither": True,
        "strip_fonts": True,
        "clean_css": True,
        "image_quality": 75,
    },
    "x4": {
        "name": "Xteink X4",
        "max_width": 480,
        "max_height": 800,
        "grayscale": True,
        "dither": True,
        "strip_fonts": True,
        "clean_css": True,
        "image_quality": 75,
    },
    "kindle": {
        "name": "Kindle Paperwhite / Oasis",
        "max_width": 1072,
        "max_height": 1448,
        "grayscale": True,
        "dither": False,
        "strip_fonts": True,
        "clean_css": True,
        "image_quality": 80,
    },
    "kobo": {
        "name": "Kobo Clara / Libra",
        "max_width": 1264,
        "max_height": 1680,
        "grayscale": True,
        "dither": False,
        "strip_fonts": True,
        "clean_css": True,
        "image_quality": 80,
    },
    "eink": {
        "name": "Generic E-Ink",
        "max_width": 1200,
        "max_height": 1600,
        "grayscale": True,
        "dither": False,
        "strip_fonts": True,
        "clean_css": True,
        "image_quality": 80,
    },
    "generic": {
        "name": "Generic E-Ink",
        "max_width": 1200,
        "max_height": 1600,
        "grayscale": True,
        "dither": False,
        "strip_fonts": True,
        "clean_css": True,
        "image_quality": 80,
    },
}

FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2", ".eot"}
FONT_MEDIA_TYPES = {
    "application/font-sfnt",
    "application/x-font-ttf",
    "application/x-font-truetype",
    "application/x-font-opentype",
    "application/vnd.ms-opentype",
    "font/ttf",
    "font/otf",
    "font/woff",
    "font/woff2",
    "application/font-woff",
    "application/font-woff2",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def get_preset(preset_key: str | None) -> dict[str, Any]:
    """Resolves a preset key or returns default generic e-ink preset."""
    if not preset_key:
        return DEVICE_PRESETS["generic"]
    normalized = preset_key.lower().strip()
    return DEVICE_PRESETS.get(normalized, DEVICE_PRESETS["generic"])


def optimize_image(
    img_bytes: bytes,
    max_width: int = 1200,
    max_height: int = 1600,
    grayscale: bool = True,
    dither: bool = False,
    quality: int = 80,
) -> bytes:
    """Resizes, converts to e-ink grayscale/dithered, and compresses an image."""
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            img = ImageOps.exif_transpose(img)

            # Preserve transparency if PNG with alpha, otherwise convert to RGB
            if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            ):
                has_alpha = True
            else:
                has_alpha = False
                if img.mode != "RGB":
                    img = img.convert("RGB")

            # Resize if larger than target bounding box
            orig_w, orig_h = img.size
            if orig_w > max_width or orig_h > max_height:
                img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            # Convert to Grayscale
            if grayscale:
                if has_alpha:
                    # Keep PNG format for transparent graphics
                    alpha = img.split()[-1]
                    gray = img.convert("L")
                    gray.putalpha(alpha)
                    out_buf = io.BytesIO()
                    gray.save(out_buf, format="PNG", optimize=True)
                    return out_buf.getvalue()
                else:
                    gray = img.convert("L")
                    if dither:
                        # 16-level grayscale for crisp e-ink rendering
                        gray = gray.quantize(
                            colors=16, dither=Image.Dither.FLOYDSTEINBERG
                        ).convert("L")

                    out_buf = io.BytesIO()
                    gray.save(out_buf, format="JPEG", quality=quality, optimize=True)
                    compressed = out_buf.getvalue()
                    # Return only if compressed version is smaller or converted
                    return compressed if len(compressed) < len(img_bytes) else img_bytes

            out_buf = io.BytesIO()
            fmt = "PNG" if has_alpha else "JPEG"
            img.save(out_buf, format=fmt, quality=quality, optimize=True)
            return out_buf.getvalue()

    except Exception as e:
        logger.debug("Failed to optimize image: %s", e)
        return img_bytes


def clean_css_content(css: str, strip_fonts: bool = True) -> str:
    """Removes @font-face rules and intrusive reader-breaking CSS."""
    if strip_fonts:
        # Strip all @font-face blocks
        css = re.sub(
            r"@font-face\s*\{[^}]*\}",
            "",
            css,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Strip font-family overrides in body/html to let e-reader pick font
        css = re.sub(
            r"(body|html)\s*\{([^}]*?)font-family\s*:[^;]+;?",
            r"\1 {\2",
            css,
            flags=re.DOTALL | re.IGNORECASE,
        )

    # Clean forced white/black colors on text/background for seamless dark/light mode
    css = re.sub(
        r"(color|background-color)\s*:\s*(#000(?:000)?|#fff(?:fff)?|black|white)\s*;?",
        "",
        css,
        flags=re.IGNORECASE,
    )
    return css


def clean_html_content(html: str, strip_fonts: bool = True) -> str:
    """Cleans inline styling and font references in HTML/XHTML."""
    if strip_fonts:
        html = re.sub(
            r"@font-face\s*\{[^}]*\}",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return html


def optimize_epub(
    source_path: Path,
    output_path: Path,
    preset_key: str = "generic",
) -> Path:
    """Reads a source EPUB, strips fonts, optimizes images, cleans CSS, and writes

    an optimized e-ink EPUB. Original file is never modified.
    """
    preset = get_preset(preset_key)
    max_w = preset.get("max_width", 1200)
    max_h = preset.get("max_height", 1600)
    grayscale = preset.get("grayscale", True)
    dither = preset.get("dither", False)
    strip_fonts = preset.get("strip_fonts", True)
    clean_css = preset.get("clean_css", True)
    img_quality = preset.get("image_quality", 80)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".epub", dir=output_path.parent
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)

    try:
        with (
            zipfile.ZipFile(source_path, "r") as src_zip,
            zipfile.ZipFile(tmp_path, "w") as dst_zip,
        ):
            # 1. First, ensure mimetype is written uncompressed
            if "mimetype" in src_zip.namelist():
                mimetype_bytes = src_zip.read("mimetype")
            else:
                mimetype_bytes = b"application/epub+zip"
            dst_zip.writestr(
                "mimetype", mimetype_bytes, compress_type=zipfile.ZIP_STORED
            )

            # 2. Locate OPF file from META-INF/container.xml
            opf_path = None
            if "META-INF/container.xml" in src_zip.namelist():
                container_data = src_zip.read("META-INF/container.xml")
                try:
                    c_tree = ET.fromstring(container_data)
                    for rf in c_tree.findall(
                        ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
                    ):
                        if (
                            rf.attrib.get("media-type")
                            == "application/oebps-package+xml"
                        ):
                            opf_path = rf.attrib.get("full-path")
                            break
                except Exception as e:
                    logger.debug("Failed parsing container.xml: %s", e)

            # 3. Process OPF manifest to identify fonts and images
            font_hrefs = set()
            modified_opf_bytes = None

            if opf_path and opf_path in src_zip.namelist():
                opf_data = src_zip.read(opf_path)
                try:
                    ET.register_namespace("", "http://www.idpf.org/2007/opf")
                    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
                    ET.register_namespace("opf", "http://www.idpf.org/2007/opf")

                    opf_tree = ET.fromstring(opf_data)
                    opf_dir = Path(opf_path).parent if "/" in opf_path else Path("")

                    manifest = opf_tree.find("{http://www.idpf.org/2007/opf}manifest")
                    if manifest is not None:
                        for item in list(manifest):
                            href = item.attrib.get("href", "")
                            media_type = item.attrib.get("media-type", "").lower()
                            ext = Path(href).suffix.lower()

                            is_font = (
                                ext in FONT_EXTENSIONS
                                or media_type in FONT_MEDIA_TYPES
                                or "font" in media_type
                            )

                            if is_font and strip_fonts:
                                # Resolve full zip relative path
                                resolved_zip_path = (
                                    str(opf_dir / href) if str(opf_dir) != "." else href
                                )
                                font_hrefs.add(resolved_zip_path)
                                font_hrefs.add(href)
                                manifest.remove(item)

                    modified_opf_bytes = ET.tostring(
                        opf_tree, encoding="utf-8", xml_declaration=True
                    )
                except Exception as e:
                    logger.debug("Error processing OPF manifest: %s", e)
                    modified_opf_bytes = opf_data

            # 4. Process and write all other files
            for item in src_zip.infolist():
                name = item.filename
                if name == "mimetype":
                    continue

                ext = Path(name).suffix.lower()

                # Skip stripped font files
                if strip_fonts and (
                    ext in FONT_EXTENSIONS
                    or name in font_hrefs
                    or any(name.endswith(f) for f in font_hrefs)
                ):
                    logger.debug("Stripping font: %s", name)
                    continue

                # Substitute modified OPF
                if name == opf_path and modified_opf_bytes is not None:
                    dst_zip.writestr(
                        name, modified_opf_bytes, compress_type=zipfile.ZIP_DEFLATED
                    )
                    continue

                raw_bytes = src_zip.read(name)

                # CSS files
                if ext == ".css" and clean_css:
                    try:
                        css_text = raw_bytes.decode("utf-8", errors="replace")
                        cleaned = clean_css_content(css_text, strip_fonts=strip_fonts)
                        dst_zip.writestr(
                            name,
                            cleaned.encode("utf-8"),
                            compress_type=zipfile.ZIP_DEFLATED,
                        )
                    except Exception:
                        dst_zip.writestr(
                            name, raw_bytes, compress_type=zipfile.ZIP_DEFLATED
                        )

                # HTML / XHTML files
                elif ext in (".html", ".xhtml", ".htm") and clean_css:
                    try:
                        html_text = raw_bytes.decode("utf-8", errors="replace")
                        cleaned = clean_html_content(html_text, strip_fonts=strip_fonts)
                        dst_zip.writestr(
                            name,
                            cleaned.encode("utf-8"),
                            compress_type=zipfile.ZIP_DEFLATED,
                        )
                    except Exception:
                        dst_zip.writestr(
                            name, raw_bytes, compress_type=zipfile.ZIP_DEFLATED
                        )

                # Image files
                elif ext in IMAGE_EXTENSIONS:
                    opt_bytes = optimize_image(
                        raw_bytes,
                        max_width=max_w,
                        max_height=max_h,
                        grayscale=grayscale,
                        dither=dither,
                        quality=img_quality,
                    )
                    dst_zip.writestr(
                        name, opt_bytes, compress_type=zipfile.ZIP_DEFLATED
                    )

                else:
                    # Pass through unchanged
                    dst_zip.writestr(
                        name, raw_bytes, compress_type=zipfile.ZIP_DEFLATED
                    )

        # Atomically move temp file to target output path
        os.replace(tmp_path, output_path)
        logger.info("Successfully optimized EPUB for [%s]: %s", preset_key, output_path)
        return output_path

    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        logger.error("Failed to optimize EPUB %s: %s", source_path, exc)
        raise


def get_or_create_optimized_epub(
    book_id: int,
    file_path: str | Path,
    file_hash: str,
    preset_key: str,
    optimized_dir: Path,
) -> Path:
    """Fetches a cached optimized EPUB or generates it on demand.

    Original source book is never modified.
    """
    optimized_dir.mkdir(parents=True, exist_ok=True)
    clean_preset = preset_key.lower().strip() if preset_key else "generic"
    cache_filename = f"{book_id}_{file_hash[:12]}_{clean_preset}.epub"
    cached_path = optimized_dir / cache_filename

    if cached_path.exists() and cached_path.stat().st_size > 0:
        return cached_path

    # Generate on demand
    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(f"Source book not found: {source}")

    return optimize_epub(source, cached_path, preset_key=clean_preset)
