from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


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
    except Exception as exc:
        logger.warning("Failed to generate cover webp at %s: %s", output_path, exc)
        return False
