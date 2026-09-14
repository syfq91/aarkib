from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from aarkib.services.thumbnail import generate_cover_webp


def _create_test_image(mode: str = "RGB", size: tuple[int, int] = (800, 1200)) -> bytes:
    img = Image.new(mode, size, color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_generate_cover_webp_success(tmp_path: Path) -> None:
    """Verify generate_cover_webp creates a WebP image and preserves dimensions within max bounds."""
    image_bytes = _create_test_image(mode="RGB", size=(800, 1200))
    output_path = tmp_path / "covers" / "test_cover.webp"

    success = generate_cover_webp(
        image_bytes, output_path, max_width=600, max_height=900
    )
    assert success is True
    assert output_path.exists()

    with Image.open(output_path) as img:
        assert img.format == "WEBP"
        w, h = img.size
        assert w <= 600
        assert h <= 900


def test_generate_cover_webp_rgba_conversion(tmp_path: Path) -> None:
    """Verify generate_cover_webp properly converts RGBA images with transparency to RGB WebP."""
    image_bytes = _create_test_image(mode="RGBA", size=(400, 400))
    output_path = tmp_path / "rgba_cover.webp"

    success = generate_cover_webp(image_bytes, output_path)
    assert success is True
    assert output_path.exists()

    with Image.open(output_path) as img:
        assert img.format == "WEBP"
        assert img.mode == "RGB"


def test_generate_cover_webp_corrupt_data(tmp_path: Path) -> None:
    """Verify generate_cover_webp returns False and does not raise on corrupt image bytes."""
    corrupt_bytes = b"not_a_valid_image_file_data"
    output_path = tmp_path / "corrupt.webp"

    success = generate_cover_webp(corrupt_bytes, output_path)
    assert success is False
    assert not output_path.exists()
