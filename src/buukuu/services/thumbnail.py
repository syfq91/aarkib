from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


def generate_cover_webp(
    image_bytes: bytes, output_path: Path, max_width: int = 600, max_height: int = 900
) -> bool:
    """Saves and optimizes cover image bytes to WebP format."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            img.save(output_path, "WEBP", quality=85, method=6)
        return True
    except Exception:
        return False
