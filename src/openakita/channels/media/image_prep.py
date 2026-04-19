"""
Image preprocessing — ensures images are at a safe size before being embedded into LLM context.

All entry points that inject base64-encoded images into messages (view_image,
browser_screenshot, IM images, media handler, etc.) should call
prepare_image_for_context() rather than encoding images themselves.
"""

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BASE64_BYTES = 800_000  # base64 output cap ~800KB (~600KB decoded)
MAX_PIXELS = 1_200_000  # pixel cap ~1280x940
JPEG_INITIAL_QUALITY = 85
JPEG_MIN_QUALITY = 40
_QUALITY_STEP = 10


def prepare_image_for_context(
    raw_bytes: bytes,
    *,
    media_type: str = "image/jpeg",
    max_base64_bytes: int = MAX_BASE64_BYTES,
    max_pixels: int = MAX_PIXELS,
) -> tuple[str, str, int, int] | None:
    """
    Process raw image bytes into base64 data suitable for LLM context.

    Returns:
        (base64_data, media_type, width, height)  on success
        None                                       if the image cannot be compressed to a safe size
    """
    estimated_b64_size = (len(raw_bytes) * 4 + 2) // 3
    if estimated_b64_size > max_base64_bytes:
        result = _compress_with_pil(raw_bytes, max_base64_bytes, max_pixels)
        if result is not None:
            return result
        logger.warning(
            f"[ImagePrep] Image too large (~{estimated_b64_size} b64 chars) "
            f"and PIL unavailable or compression failed, cannot embed inline"
        )
        return None

    b64_data = base64.b64encode(raw_bytes).decode("ascii")
    w, h = _probe_dimensions(raw_bytes)
    return b64_data, media_type, w, h


def prepare_image_file_for_context(
    file_path: str | Path,
    *,
    max_base64_bytes: int = MAX_BASE64_BYTES,
    max_pixels: int = MAX_PIXELS,
) -> tuple[str, str, int, int] | None:
    """Load and preprocess an image from a file path."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return None

    ext = p.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    media_type = mime_map.get(ext, "image/jpeg")

    try:
        raw = p.read_bytes()
    except OSError as e:
        logger.error(f"[ImagePrep] Failed to read {file_path}: {e}")
        return None

    return prepare_image_for_context(
        raw,
        media_type=media_type,
        max_base64_bytes=max_base64_bytes,
        max_pixels=max_pixels,
    )


def _compress_with_pil(
    raw_bytes: bytes,
    max_base64_bytes: int,
    max_pixels: int,
) -> tuple[str, str, int, int] | None:
    """Iteratively compress an image using PIL until the base64 size meets the limit."""
    try:
        import io

        from PIL import Image
    except ImportError:
        return None

    try:
        img = Image.open(io.BytesIO(raw_bytes))
    except Exception as e:
        logger.warning(f"[ImagePrep] PIL cannot open image: {e}")
        return None

    w, h = img.size

    if w * h > max_pixels:
        ratio = (max_pixels / (w * h)) ** 0.5
        w, h = int(w * ratio), int(h * ratio)
        img = img.resize((w, h), Image.LANCZOS)

    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    quality = JPEG_INITIAL_QUALITY
    while quality >= JPEG_MIN_QUALITY:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        if len(b64) <= max_base64_bytes:
            logger.debug(f"[ImagePrep] Compressed to {len(b64)} b64 chars (q={quality}, {w}x{h})")
            return b64, "image/jpeg", w, h
        quality -= _QUALITY_STEP

    # Quality at minimum but still over limit; further shrink resolution
    for shrink in (0.7, 0.5, 0.3):
        sw, sh = int(w * shrink), int(h * shrink)
        if sw < 100 or sh < 100:
            break
        small = img.resize((sw, sh), Image.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=JPEG_MIN_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        if len(b64) <= max_base64_bytes:
            logger.info(
                f"[ImagePrep] Aggressively compressed to {len(b64)} b64 chars "
                f"({sw}x{sh}, q={JPEG_MIN_QUALITY})"
            )
            return b64, "image/jpeg", sw, sh

    logger.warning("[ImagePrep] Cannot compress image within limits even at minimum quality")
    return None


def _probe_dimensions(raw_bytes: bytes) -> tuple[int, int]:
    """Try to get image dimensions; returns (0, 0) on failure."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(raw_bytes))
        return img.size
    except Exception:
        return 0, 0
