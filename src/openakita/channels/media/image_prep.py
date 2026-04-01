"""
图片预处理 — 确保图片在嵌入 LLM 上下文前处于安全大小。

所有将图片 base64 注入消息的入口（view_image、browser_screenshot、IM 图片、
media handler 等）都应调用 prepare_image_for_context() 而非自行编码。
"""

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BASE64_BYTES = 800_000  # base64 产出上限 ~800KB（解码后 ~600KB）
MAX_PIXELS = 1_200_000  # 像素上限 ~1280×940
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
    将原始图片字节处理为适合 LLM 上下文的 base64 数据。

    Returns:
        (base64_data, media_type, width, height)  成功
        None                                       无法压缩到安全大小
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
    """从文件路径加载并预处理图片。"""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return None

    ext = p.suffix.lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }
    media_type = mime_map.get(ext, "image/jpeg")

    try:
        raw = p.read_bytes()
    except OSError as e:
        logger.error(f"[ImagePrep] Failed to read {file_path}: {e}")
        return None

    return prepare_image_for_context(
        raw, media_type=media_type,
        max_base64_bytes=max_base64_bytes, max_pixels=max_pixels,
    )


def _compress_with_pil(
    raw_bytes: bytes,
    max_base64_bytes: int,
    max_pixels: int,
) -> tuple[str, str, int, int] | None:
    """使用 PIL 迭代压缩图片直到 base64 大小满足限制。"""
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
            logger.debug(
                f"[ImagePrep] Compressed to {len(b64)} b64 chars "
                f"(q={quality}, {w}x{h})"
            )
            return b64, "image/jpeg", w, h
        quality -= _QUALITY_STEP

    # 质量已最低仍超限，进一步缩小分辨率
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
    """尝试获取图片尺寸，失败返回 (0, 0)。"""
    try:
        import io

        from PIL import Image
        img = Image.open(io.BytesIO(raw_bytes))
        return img.size
    except Exception:
        return 0, 0
